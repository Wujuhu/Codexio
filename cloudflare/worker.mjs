// Native Fetch / Web Crypto / D1 only. Read-only projection: no Codex controls.
const json = (value, status = 200) => Response.json(value, {status, headers:{'Cache-Control':'private, no-store','Retry-After':'60'}});
const encoder = new TextEncoder();
const digest = async value => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',encoder.encode(value))),x=>x.toString(16).padStart(2,'0')).join('');
const id = value => typeof value === 'string' && /^[a-zA-Z0-9_-]{16,64}$/.test(value);
const requestID = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const secret = value => typeof value === 'string' && /^[a-zA-Z0-9_-]{43}$/.test(value);
const datasets = ['live','recent','trends'];
const capability = 'request-details-v1';
const requestKindCapability = 'request-kinds-v1';
const imageCapability = 'request-images-v1';
const detailLimit = 1048576, chunkBytes = 65536, detailRows = 64, hostBytes = 8 * 1048576, retention = 7 * 86400;
const imageLimit = 1048576, imageEnvelopeLimit = 1500000, imageRows = 128, imageHostBytes = 32 * 1048576, imageRetention = 3 * 86400;
const imageMimes = new Set(['image/jpeg','image/png','image/gif','image/webp','image/heic','image/tiff','image/bmp']);
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const dimensions = (width,height) => Number.isSafeInteger(width) && Number.isSafeInteger(height) && width > 0 && height > 0 && Math.max(width,height) <= 2048 && width*height <= 4194304;
const fields = new Set(['name','timeZone','observed','task','runningCount','today','five','week','remaining','reset','retained','tokens','cost','requests','costComplete','hitRate','id','started','status','preview','model','effort','speed','duration','kind','daily','periods','start','metric','days','total','models']);
function validPayload(value, depth = 0) {
  if (depth > 8) return false;
  if (value === null || typeof value === 'boolean') return true;
  if (typeof value === 'number') return Number.isFinite(value) && Math.abs(value) <= Number.MAX_SAFE_INTEGER;
  if (typeof value === 'string') return encoder.encode(value).length <= 240;
  if (Array.isArray(value)) return value.length <= 200 && value.every(x=>validPayload(x,depth+1));
  return typeof value === 'object' && Object.entries(value).every(([key,item])=>fields.has(key)&&validPayload(item,depth+1));
}
async function body(request, max = 262144) {
  if (!request.headers.get('content-type')?.startsWith('application/json')) throw new Error('body');
  const reader = request.body?.getReader(); if (!reader) throw new Error('body');
  let length = 0; const chunks=[];
  while(true) {const part=await reader.read(); if(part.done) break; length+=part.value.length; if(length>max){await reader.cancel();throw new Error('body');} chunks.push(part.value);}
  const data=new Uint8Array(length);let offset=0;for(const chunk of chunks){data.set(chunk,offset);offset+=chunk.length;}
  return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(data));
}

// A block is an atomic reservation from the SAME global daily counter. Only
// this isolate can spend its remaining tokens. Resets discard unused tokens;
// never reconstruct them from D1 or treat `budget.n` as exact request analytics.
let allowance = null;
async function admit(env) {
  while (true) {
    const day = new Date().toISOString().slice(0,10);
    const configured = Number(env.DAILY_REQUEST_BUDGET ?? 20000);
    const cap = Number.isSafeInteger(configured) && configured > 0 ? Math.min(20000,configured) : 20000;
    if (!allowance || allowance.day !== day || allowance.cap !== cap || allowance.db !== env.DB) allowance = {day,cap,db:env.DB,remaining:0,pending:null,exhausted:false};
    const state = allowance;
    if (state.remaining > 0) { state.remaining--; return true; }
    if (state.exhausted) return false;
    if (!state.pending) {
      const block = Math.min(32,cap);
      state.pending = env.DB.prepare('INSERT INTO budget(day,n) VALUES(?,?) ON CONFLICT(day) DO UPDATE SET n=n+? WHERE n <= ?-? RETURNING n')
        .bind(day,block,block,cap,block).first().then(row=>{
          // A cold/day-reset isolate can only waste a reservation, not reuse it.
          if (allowance === state) { state.remaining = row ? block : 0; state.exhausted = !row; }
        }).finally(()=>{state.pending=null;});
    }
    await state.pending; // Single flight within this isolate, atomic across all isolates.
  }
}
let storageSchema = null;
async function storage(env) {
  if (!storageSchema || storageSchema.db !== env.DB || storageSchema.until <= Date.now()) {
    const state = {db:env.DB,until:Date.now()+300000,promise:null};
    state.promise = env.DB.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('request_details','request_images','image_lifetimes','payload_retention')").all().then(value=>new Set(value.results.map(row=>row.name))).catch(()=>new Set());
    storageSchema = state;
  }
  return storageSchema.promise;
}
async function hasDetails(env) { const tables=await storage(env); return env.DETAILS_ENABLED === '1' && tables.has('request_details') && tables.has('payload_retention'); }
async function hasImages(env) { const tables=await storage(env); return await hasDetails(env) && env.IMAGES_ENABLED === '1' && tables.has('request_images') && tables.has('image_lifetimes'); }
async function capabilities(env) { return [requestKindCapability,...(await hasDetails(env)?[capability]:[]),...(await hasImages(env)?[imageCapability]:[])]; }
function validImageReference(item) {
  if(!object(item) || Object.keys(item).some(key=>!['id','name','mime','placement','width','height','expires','availability'].includes(key))
    || !requestID(item.id) || typeof item.name!=='string' || encoder.encode(item.name).length>240
    || !['user','final'].includes(item.placement) || !Number.isSafeInteger(item.expires) || item.expires<=0
    || !['available','unavailable','expired'].includes(item.availability))return false;
  // A missing/expired source keeps its original type as metadata only. It
  // cannot authorize an upload or relax raster validation for readable bytes.
  const placeholder=item.availability!=='available' && item.width===0 && item.height===0;
  const originalMime=placeholder && typeof item.mime==='string' && encoder.encode(item.mime).length<=100 && /^image\/[A-Za-z0-9][A-Za-z0-9.+_-]*$/.test(item.mime);
  return (dimensions(item.width,item.height) || placeholder) && (imageMimes.has(item.mime) || originalMime);
}

function validDetail(value, record, now) {
  const keys = new Set(['id','started','completed','status','user','final','userComplete','finalComplete','availability','attachments','images','full']);
  if (!object(value) || Object.keys(value).some(key=>!keys.has(key)) || value.id !== record) return false;
  if (!Number.isFinite(value.started) || value.started <= 0 || value.started > now+300 || value.completed != null && (!Number.isFinite(value.completed) || value.completed < value.started || value.completed > now+300)) return false;
  if (Math.max(value.started,value.completed ?? value.started) <= now-retention) return false;
  if (!['running','completed','aborted','failed','unknown',''].includes(value.status) || !['available','partial','unavailable','capacity'].includes(value.availability)) return false;
  if (typeof value.user !== 'string' || typeof value.final !== 'string' || typeof value.userComplete !== 'boolean' || typeof value.finalComplete !== 'boolean' || typeof value.full !== 'boolean') return false;
  if (!Array.isArray(value.attachments) || value.attachments.length > 6) return false;
  if (value.images != null && (!Array.isArray(value.images) || value.images.length > 32 || !value.images.every(validImageReference) || new Set(value.images.map(item=>item.id)).size !== value.images.length)) return false;
  let images = 0;
  return value.attachments.every(item=>{
    if (!object(item) || Object.keys(item).some(key=>!['id','name','mime','thumbnail'].includes(key)) || !requestID(item.id) || typeof item.name !== 'string' || encoder.encode(item.name).length > 240 || item.mime != null && (typeof item.mime !== 'string' || item.mime.length > 100)) return false;
    if (item.thumbnail == null) return true;
    if (++images > 2 || typeof item.thumbnail !== 'string' || item.thumbnail.length > 16384 || !/^[A-Za-z0-9+/]+={0,2}$/.test(item.thumbnail)) return false;
    try { const bytes=atob(item.thumbnail); return bytes.length <= 12288 && bytes.charCodeAt(0)===255 && bytes.charCodeAt(1)===216; } catch { return false; }
  });
}
// Validate bounded container headers without decoding images in the Worker.
// Mac exports a single raster frame; readers also decode and verify dimensions.
function rasterDimensions(bytes,mime) {
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength), length=bytes.length;
  const text=(start,count)=>String.fromCharCode(...bytes.subarray(start,start+count));
  const u16=(at,little=false)=>view.getUint16(at,little), u32=(at,little=false)=>view.getUint32(at,little);
  try {
    if(mime==='image/jpeg') {
      if(length<4 || u16(0)!==0xffd8 || u16(length-2)!==0xffd9)return null;
      let at=2, size=null;
      while(at+4<=length) {
        if(bytes[at++]!==255)return null;while(bytes[at]===255)at++;
        const marker=bytes[at++];if(marker===0xda)return size;
        const count=u16(at);if(count<2 || at+count>length)return null;
        if(marker===0xe2 && text(at+2,4)==='MPF\0')return null;
        if([0xc0,0xc1,0xc2].includes(marker)) {if(count<8 || size)return null;size=[u16(at+5),u16(at+3)];}
        at+=count;
      }
    } else if(mime==='image/png') {
      if(text(0,8)!=='\x89PNG\r\n\x1a\n')return null;
      let at=8,size=null,data=false;
      while(at+12<=length) {
        const count=u32(at), tag=text(at+4,4);if(at+12+count>length || ['acTL','fcTL','fdAT'].includes(tag))return null;
        if(tag==='IHDR') {if(at!==8 || count!==13)return null;size=[u32(at+8),u32(at+12)];}
        if(tag==='IDAT')data=true;
        if(tag==='IEND')return count===0 && at+12===length && data?size:null;
        at+=count+12;
      }
    } else if(mime==='image/gif') {
      if(!['GIF87a','GIF89a'].includes(text(0,6)) || length<14)return null;
      const size=[u16(6,true),u16(8,true)];let at=13+(bytes[10]&128?3*(1<<((bytes[10]&7)+1)):0),frames=0;
      const blocks=()=>{while(at<length){const count=bytes[at++];if(count===0)return true;at+=count;}return false;};
      while(at<length) {
        const tag=bytes[at++];if(tag===0x3b)return frames===1 && at===length?size:null;
        if(tag===0x21) {at++;if(!blocks())return null;}
        else if(tag===0x2c) {if(++frames>1 || at+9>length || u16(at+4,true)+u16(at,true)>size[0] || u16(at+6,true)+u16(at+2,true)>size[1])return null;const flags=bytes[at+8];at+=9+(flags&128?3*(1<<((flags&7)+1)):0);at++;if(!blocks())return null;}
        else return null;
      }
    } else if(mime==='image/webp') {
      if(text(0,4)!=='RIFF' || text(8,4)!=='WEBP' || u32(4,true)+8!==length)return null;
      let at=12,size=null,canvas=null,frames=0;
      const u24=offset=>bytes[offset]+(bytes[offset+1]<<8)+(bytes[offset+2]<<16);
      while(at+8<=length) {
        const tag=text(at,4), count=u32(at+4,true), start=at+8;if(start+count>length || ['ANIM','ANMF'].includes(tag))return null;
        if(tag==='VP8X') {if(count!==10 || bytes[start]&2)return null;canvas=[u24(start+4)+1,u24(start+7)+1];}
        if(tag==='VP8 ') {if(++frames>1 || count<10 || text(start+3,3)!=='\x9d\x01\x2a')return null;size=[u16(start+6,true)&16383,u16(start+8,true)&16383];}
        if(tag==='VP8L') {if(++frames>1 || count<5 || bytes[start]!==0x2f)return null;const bits=u32(start+1,true);size=[(bits&16383)+1,((bits>>>14)&16383)+1];}
        at=start+count+(count&1);
      }
      return at===length && size && (!canvas || canvas.every((value,index)=>value===size[index]))?size:null;
    } else if(mime==='image/bmp') {
      if(length<54 || text(0,2)!=='BM' || u32(14,true)<40 || u32(10,true)>=length || ![0,3].includes(u32(30,true)))return null;
      return [view.getInt32(18,true),Math.abs(view.getInt32(22,true))];
    } else if(mime==='image/tiff') {
      const little=text(0,2)==='II';if(!little && text(0,2)!=='MM' || u16(2,little)!==42)return null;
      const at=u32(4,little), count=u16(at,little);if(count>256 || at+2+12*count+4>length || u32(at+2+12*count,little)!==0)return null;
      let width,height;
      for(let i=0;i<count;i++){const entry=at+2+12*i, tag=u16(entry,little),type=u16(entry+2,little);if(tag===330)return null;if(tag!==256&&tag!==257)continue;if(u32(entry+4,little)!==1 || ![3,4].includes(type))return null;const value=type===3?u16(entry+8,little):u32(entry+8,little);if(tag===256)width=value;else height=value;}
      return [width,height];
    } else if(mime==='image/heic') {
      if(length<24 || text(4,4)!=='ftyp')return null;
      const ftyp=u32(0);if(ftyp<20 || ftyp>length)return null;
      const brands=[text(8,4)];for(let at=16;at+4<=ftyp;at+=4)brands.push(text(at,4));
      if(!brands.some(value=>['heic','heix'].includes(value)) || brands.some(value=>['msf1','hevc','hevx','heim','heis','hevm','hevs'].includes(value)))return null;
      const sizes=[];let boxes=0;
      const walk=(from,to,depth=0)=>{if(depth>5)return false;for(let at=from;at<to;){if(++boxes>1024 || at+8>to)return false;const count=u32(at),tag=text(at+4,4);if(count<8 || at+count>to || tag==='moov')return false;if(tag==='ispe'){if(count!==20)return false;const size=[u32(at+12),u32(at+16)];if(!dimensions(...size))return false;sizes.push(size);}if(['meta','iprp','ipco'].includes(tag) && !walk(at+8+(tag==='meta'?4:0),at+count,depth+1))return false;at+=count;}return true;};
      if(!walk(0,length) || !sizes.length)return null;
      return sizes.reduce((largest,size)=>size[0]*size[1]>largest[0]*largest[1]?size:largest);
    }
  } catch { return null; }
  return null;
}
function validImage(value,record,now) {
  if(!object(value) || Object.keys(value).some(key=>!['id','request','mime','width','height','created','expires','data'].includes(key)) || value.id!==record || !requestID(value.request) || !imageMimes.has(value.mime) || !dimensions(value.width,value.height))return false;
  if(!Number.isSafeInteger(value.created) || value.created<=0 || value.created>now+300 || !Number.isSafeInteger(value.expires) || value.expires<=value.created || value.expires>value.created+imageRetention || value.expires<=now)return false;
  if(typeof value.data!=='string' || value.data.length>4*Math.ceil(imageLimit/3) || value.data.length%4!==0 || !/^[A-Za-z0-9+/]+={0,2}$/.test(value.data))return false;
  try {const raw=atob(value.data);if(raw.length<1 || raw.length>imageLimit)return false;const bytes=Uint8Array.from(raw,value=>value.charCodeAt(0)), size=rasterDimensions(bytes,value.mime);return size && size[0]===value.width && size[1]===value.height;} catch {return false;}
}
function validDataset(value,dataset) {
  if(!validPayload(value))return false;
  const requestKeys=new Set(['id','started','status','preview','model','effort','speed','tokens','cost','duration','kind']);
  const summaryKeys=new Set(['remaining','reset','observed','retained','tokens','cost','requests','costComplete','hitRate']);
  const safeSummary=item=>object(item) && Object.keys(item).every(key=>summaryKeys.has(key));
  const safeRequest=item=>object(item) && requestID(item.id) && Number.isFinite(item.started) && item.started>0 && Object.keys(item).every(key=>requestKeys.has(key));
  if(dataset==='recent')return Array.isArray(value) && value.every(safeRequest);
  if(dataset==='live')return object(value) && Object.keys(value).every(key=>['name','timeZone','observed','task','runningCount','today','five','week'].includes(key)) && (value.task==null || safeRequest(value.task)) && safeSummary(value.today) && safeSummary(value.five) && safeSummary(value.week);
  const aggregateKeys=new Set(['daily','periods','id','start','metric','days','total','models','name','tokens','cost','requests','costComplete','hitRate']);
  const aggregate=item=>!object(item)&&!Array.isArray(item) || Object.entries(item).every(([key,child])=>(Array.isArray(item)||aggregateKeys.has(key))&&aggregate(child));
  return object(value) && Array.isArray(value.daily) && value.daily.length<=90 && Array.isArray(value.periods) && value.periods.length<=3 && aggregate(value);
}
function retainPayload(kind,payload,now) {
  let value=JSON.parse(payload),changed=false;const deadlines=[];
  const recent=item=>object(item) && Number.isFinite(item.started) && item.started>now-retention && item.started<=now+300;
  if(kind==='recent') {const retained=value.filter(recent);changed=retained.length!==value.length;value=retained;deadlines.push(...value.map(item=>item.started+retention));}
  if(kind==='live' && value.task!=null) {if(!recent(value.task)){value.task=null;changed=true;}else deadlines.push(value.task.started+retention);}
  if(kind==='detail') {
    for(const item of value.attachments) if(item.thumbnail!=null) {if(value.started+imageRetention<=now){delete item.thumbnail;changed=true;}else deadlines.push(value.started+imageRetention);}
    for(const item of value.images ?? []) if(item.availability==='available') {if(item.expires<=now){item.availability='expired';changed=true;}else deadlines.push(item.expires);}
  }
  return {payload:changed?JSON.stringify(value):payload,due:deadlines.length?Math.min(...deadlines):null};
}
function retentionStatement(db,host,kind,name,sourceRevision,sourceDigest,due,revision,hash,writerHash=null) {
  const table=kind==='detail'?'request_details':'datasets', key=kind==='detail'?'id':'dataset';
  return db.prepare(`INSERT INTO payload_retention(host,kind,name,source_revision,source_digest,due) SELECT ?,?,?,?,?,?
    WHERE EXISTS(SELECT 1 FROM ${table} WHERE host=? AND ${key}=? AND revision=? AND digest=?) ${writerHash?'AND EXISTS(SELECT 1 FROM hosts WHERE id=? AND writer_hash=?)':''}
    ON CONFLICT(host,kind,name) DO UPDATE SET source_revision=excluded.source_revision,source_digest=excluded.source_digest,due=excluded.due WHERE excluded.source_revision>=payload_retention.source_revision`)
    .bind(host,kind,name,sourceRevision,sourceDigest,due,host,name,revision,hash,...(writerHash?[host,writerHash]:[]));
}
async function retainRow(db,kind,row,now,writerHash=null) {
  const name=kind==='detail'?row.id:row.dataset, retained=retainPayload(kind==='detail'?'detail':name,row.payload,now);
  const hash=retained.payload===row.payload?row.digest:await digest(retained.payload), revision=row.revision+(hash===row.digest?0:1);
  if(!Number.isSafeInteger(revision))throw new Error('revision');
  const sourceRevision=row.source_revision??row.revision, sourceDigest=row.source_digest??row.digest;
  const actions=[];
  if(hash!==row.digest) {
    const table=kind==='detail'?'request_details':'datasets',key=kind==='detail'?'id':'dataset';
    actions.push(db.prepare(`UPDATE ${table} SET revision=?,digest=?,payload=?${kind==='detail'?',bytes=?':''} WHERE host=? AND ${key}=? AND revision=? AND digest=? ${writerHash?'AND EXISTS(SELECT 1 FROM hosts WHERE id=? AND writer_hash=?)':''}`)
      .bind(revision,hash,retained.payload,...(kind==='detail'?[encoder.encode(retained.payload).length]:[]),row.host,name,row.revision,row.digest,...(writerHash?[row.host,writerHash]:[])));
  }
  actions.push(retentionStatement(db,row.host,kind,name,sourceRevision,sourceDigest,retained.due,revision,hash,writerHash));
  const result=await db.batch(actions);
  if(result.at(-1).meta?.changes!==1)throw new Error('retention changed');
  return {...row,payload:retained.payload,digest:hash,revision,source_revision:sourceRevision,source_digest:sourceDigest};
}
async function retainDetailIfDue(db,host,record,now,writerHash=null) {
  const due=await db.prepare("SELECT due FROM payload_retention WHERE host=? AND kind='detail' AND name=? AND due<=?").bind(host,record,now).first();
  if(!due)return;
  const row=await db.prepare("SELECT d.*,r.source_revision,r.source_digest FROM request_details d LEFT JOIN payload_retention r ON r.host=d.host AND r.kind='detail' AND r.name=d.id WHERE d.host=? AND d.id=? AND d.expires>?").bind(host,record,now).first();
  if(row)await retainRow(db,'detail',row,now,writerHash);
}
function prefix(value, bytes) { return new TextDecoder().decode(encoder.encode(value).subarray(0,bytes)).replace(/\uFFFD$/,''); }
function base64Hex(hex) { let output='';for(let i=0;i<hex.length;i+=2)output+=String.fromCharCode(parseInt(hex.slice(i,i+2),16));return btoa(output); }
async function writerAuth(db, host, tokenHash) { return db.prepare('SELECT id FROM hosts WHERE id=? AND writer_hash=?').bind(host,tokenHash).first(); }
async function readerAuth(db, host, reader, tokenHash) {
  if(!id(reader)) return {error:'UNAUTHORIZED',status:401};
  const row=await db.prepare('SELECT readers.id,hosts.writer_hash AS key_state FROM readers JOIN hosts ON hosts.id=readers.host WHERE readers.host=? AND readers.id=? AND readers.token_hash=?').bind(host,reader,tokenHash).first();
  if(!row)return {error:'REVOKED',status:403};
  if(row.key_state.startsWith('disabled:'))return {error:'CLOUD_DISABLED',status:403};
  return row;
}
export default {
  async fetch(request, env) {
    try {
      const url=new URL(request.url), parts=url.pathname.split('/').filter(Boolean);
      if(url.pathname==='/health' && request.method==='GET') return json({service:'codexio-sync',protocol:1});
      if(parts[0]!=='v1') return json({error:'NOT_FOUND'},404);
      const bearer=request.headers.get('authorization')?.replace(/^Bearer /,'') ?? '';
      if(bearer.length<43 || bearer.length>160) return json({error:'UNAUTHORIZED'},401);
      if(env.RATE && !(await env.RATE.limit({key:request.headers.get('cf-connecting-ip')??'unknown'})).success) return json({error:'RATE_LIMIT'},429);
      const db=env.DB, tokenHash=await digest(bearer), now=Math.floor(Date.now()/1000);
      // Authentication precedes budget admission; invalid/revoked credentials do
      // not spend globally reserved requests. Authentication still has read cost.
      if(parts[1]==='enroll' && parts.length===2 && request.method==='POST') {
        const b=await body(request,2048);
        if(!id(b.host)||!secret(b.writer)||typeof b.name!=='string'||b.name.length>40) return json({error:'INVALID'},400);
        const writerHash=await digest(b.writer);
        const invite=await db.prepare('SELECT hash,host FROM invites WHERE hash=? AND expires>? AND (host IS NULL OR (host=? AND EXISTS(SELECT 1 FROM hosts WHERE id=? AND writer_hash=?)))').bind(tokenHash,now,b.host,b.host,writerHash).first();
        if(!invite)return json({error:'INVITE_INVALID'},403);
        if(!(await admit(env)))return json({error:'DAILY_BUDGET'},429);
        const result=await db.batch([
          db.prepare('UPDATE invites SET host=? WHERE hash=? AND expires>? AND host IS NULL').bind(b.host,tokenHash,now),
          db.prepare("INSERT INTO hosts(id,writer_hash,name) SELECT ?,?,? WHERE EXISTS(SELECT 1 FROM invites WHERE hash=? AND host=? AND expires>?) ON CONFLICT(id) DO UPDATE SET writer_hash=excluded.writer_hash,name=excluded.name WHERE hosts.writer_hash LIKE 'disabled:%' AND ?").bind(b.host,writerHash,b.name,tokenHash,b.host,now,invite.host == null ? 1 : 0),
          db.prepare('SELECT id FROM hosts WHERE id=? AND writer_hash=? AND EXISTS(SELECT 1 FROM invites WHERE hash=? AND host=? AND expires>?)').bind(b.host,writerHash,tokenHash,b.host,now)
        ]);
        return result[2].results.length ? json({ok:true}) : json({error:'INVITE_INVALID'},403);
      }
      const host=parts[2];if(parts[1]!=='hosts'||!id(host))return json({error:'INVALID'},400);
      const readerRoute = request.method==='GET' && ((parts[3]==='sync' && parts.length===4) || (['details','images'].includes(parts[3]) && parts.length===5 && requestID(parts[4])));
      let writer;
      if (readerRoute) {
        const reader=await readerAuth(db,host,url.searchParams.get('reader'),tokenHash);
        if(reader.error)return json({error:reader.error},reader.status);
      } else {
        writer=await writerAuth(db,host,tokenHash);
        if(!writer && parts[3]==='key' && parts.length===4 && request.method==='DELETE') writer=await db.prepare('SELECT id FROM hosts WHERE id=? AND writer_hash=?').bind(host,'disabled:'+tokenHash).first();
        if(!writer)return json({error:'UNAUTHORIZED'},403);
      }
      const route = (parts[3]==='capabilities' && parts.length===4 && request.method==='GET') || readerRoute ||
        (parts[3]==='key' && parts.length===4 && request.method==='DELETE') ||
        (parts[3]==='readers' && ((request.method==='PUT' && parts.length===4) || (request.method==='DELETE' && parts.length===5 && id(parts[4])))) ||
        (request.method==='PUT' && parts.length===5 && ((parts[3]==='data' && datasets.includes(parts[4])) || (['details','images'].includes(parts[3]) && requestID(parts[4]))));
      if(!route)return json({error:'NOT_FOUND'},404);
      if(!(await admit(env)))return json({error:'DAILY_BUDGET'},429);
      if(parts[3]==='capabilities') return json({action:'capabilities',capabilities:await capabilities(env)});
      if(parts[3]==='key') {
        // Enrollment permits at most one host per invite. Index lookup is bounded;
        // retain the existing identity and reader-revocation behavior.
        await db.batch([
          db.prepare('UPDATE hosts SET writer_hash=? WHERE id=? AND writer_hash=?').bind('disabled:'+tokenHash,host,tokenHash),
          db.prepare('DELETE FROM invites WHERE hash IN (SELECT hash FROM invites WHERE host=? LIMIT 8) AND EXISTS(SELECT 1 FROM hosts WHERE id=? AND writer_hash=?)').bind(host,host,'disabled:'+tokenHash)
        ]);
        return json({ok:true});
      }
      if(parts[3]==='readers' && request.method==='PUT') {
        const b=await body(request,4096);if(!id(b.id)||!secret(b.secret))return json({error:'INVALID'},400);
        const row=await db.prepare('INSERT INTO readers(host,id,token_hash) SELECT ?,?,? WHERE EXISTS(SELECT 1 FROM hosts WHERE id=? AND writer_hash=?) AND ((SELECT count(*) FROM readers WHERE host=?) < 3 OR EXISTS(SELECT 1 FROM readers WHERE host=? AND id=?)) ON CONFLICT(host,id) DO UPDATE SET token_hash=excluded.token_hash WHERE readers.token_hash!=excluded.token_hash RETURNING id').bind(host,b.id,await digest(b.secret),host,tokenHash,host,host,b.id).first();
        if(row)return json({ok:true});
        const same=await db.prepare('SELECT id FROM readers WHERE host=? AND id=? AND token_hash=?').bind(host,b.id,await digest(b.secret)).first();
        return same?json({ok:true}):json({error:'READER_LIMIT'},409);
      }
      if(parts[3]==='readers' && request.method==='DELETE') {
        await db.prepare('DELETE FROM readers WHERE host=? AND id=? AND EXISTS(SELECT 1 FROM hosts WHERE id=? AND writer_hash=?)').bind(host,parts[4],host,tokenHash).run();return json({ok:true});
      }
      if(parts[3]==='data') {
        if(!(await storage(env)).has('payload_retention'))return json({error:'RETENTION_UNSUPPORTED'},503);
        const b=await body(request), dataset=parts[4];
        if(b.dataset!==dataset||!Number.isSafeInteger(b.revision)||b.revision<1||typeof b.payload!=='string'||encoder.encode(b.payload).length>({live:8000,recent:120000,trends:80000}[dataset])||await digest(b.payload)!==b.digest)return json({error:'INVALID'},400);
        const p=JSON.parse(b.payload);if(!validDataset(p,dataset))return json({error:'INVALID'},400);
        let old=await db.prepare("SELECT d.*,r.source_revision,r.source_digest,r.due FROM datasets d LEFT JOIN payload_retention r ON r.host=d.host AND r.kind='dataset' AND r.name=d.dataset WHERE d.host=? AND d.dataset=?").bind(host,dataset).first();
        if(old && old.due!=null && old.due<=now)old=await retainRow(db,'dataset',old,now,tokenHash);
        if(old && (old.source_revision??old.revision)===b.revision && (old.source_digest??old.digest)===b.digest)return json({ok:true,revision:old.revision,digest:old.digest});
        if(old && (old.source_revision??old.revision)>=b.revision)return json({error:'REVISION_CONFLICT',revision:old.revision,digest:old.digest},409);
        const retained=retainPayload(dataset,b.payload,now), hash=retained.payload===b.payload?b.digest:await digest(retained.payload), revision=Math.max(b.revision+(hash===b.digest?0:1),(old?.revision??0)+1);
        if(!Number.isSafeInteger(revision))return json({error:'INVALID'},400);
        const result=await db.batch([
          db.prepare(`INSERT INTO datasets(host,dataset,revision,digest,payload,updated) SELECT ?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM hosts WHERE id=? AND writer_hash=?)
            ON CONFLICT(host,dataset) DO UPDATE SET revision=excluded.revision,digest=excluded.digest,payload=excluded.payload,updated=excluded.updated WHERE datasets.revision=? AND datasets.digest=? RETURNING revision`)
            .bind(host,dataset,revision,hash,retained.payload,now,host,tokenHash,old?.revision??0,old?.digest??''),
          retentionStatement(db,host,'dataset',dataset,b.revision,b.digest,retained.due,revision,hash,tokenHash)
        ]);
        return result[0].results.length?json({ok:true,revision,digest:hash}):json({error:'REVISION_CONFLICT'},409);
      }
      if(parts[3]==='images') {
        if(!(await hasImages(env)))return json({error:'IMAGES_UNSUPPORTED'},404);
        const record=parts[4];
        if(request.method==='PUT') {
          const b=await body(request,imageEnvelopeLimit), size=typeof b.payload==='string'?encoder.encode(b.payload).length:0;
          if(b.dataset!=='image-'+record || !Number.isSafeInteger(b.revision) || b.revision<1 || size<1 || size>imageEnvelopeLimit || await digest(b.payload)!==b.digest)return json({error:'INVALID'},400);
          const p=JSON.parse(b.payload);if(!validImage(p,record,now))return json({error:p?.expires<=now?'IMAGE_EXPIRED':'INVALID'},p?.expires<=now?410:400);
          const lifetime=await db.prepare('SELECT request_id,created,expires FROM image_lifetimes WHERE host=? AND id=?').bind(host,record).first();
          if(lifetime && lifetime.expires<=now)return json({error:'IMAGE_EXPIRED'},410);
          if(lifetime && (lifetime.request_id!==p.request || lifetime.created!==p.created || lifetime.expires!==p.expires))return json({error:'IMAGE_ID_CONFLICT'},409);
          const old=await db.prepare('SELECT revision,digest FROM request_images WHERE host=? AND id=?').bind(host,record).first();
          if(old?.revision===b.revision && old?.digest===b.digest)return json({ok:true,revision:old.revision,digest:old.digest});
          if(old && old.revision>=b.revision)return json({error:'REVISION_CONFLICT'},409);
          const result=await db.batch([
            db.prepare('DELETE FROM request_images WHERE host=? AND expires<=? AND EXISTS(SELECT 1 FROM hosts WHERE id=? AND writer_hash=?)').bind(host,now,host,tokenHash),
            db.prepare('DELETE FROM image_lifetimes WHERE host=? AND forget<=? AND EXISTS(SELECT 1 FROM hosts WHERE id=? AND writer_hash=?)').bind(host,now,host,tokenHash),
            db.prepare(`INSERT INTO image_lifetimes(host,id,request_id,created,expires,forget) SELECT ?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM hosts WHERE id=? AND writer_hash=?)
              AND ((SELECT count(*) FROM image_lifetimes WHERE host=?)<? OR EXISTS(SELECT 1 FROM image_lifetimes WHERE host=? AND id=?)) ON CONFLICT(host,id) DO NOTHING`)
              .bind(host,record,p.request,p.created,p.expires,p.created+retention,host,tokenHash,host,imageRows,host,record),
            db.prepare(`INSERT INTO request_images(host,id,request_id,revision,digest,payload,bytes,mime,width,height,created,expires,orphan_until) SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?
              WHERE EXISTS(SELECT 1 FROM hosts WHERE id=? AND writer_hash=?)
              AND EXISTS(SELECT 1 FROM image_lifetimes WHERE host=? AND id=? AND request_id=? AND created=? AND expires=? AND expires>?)
              AND NOT EXISTS(SELECT 1 FROM request_details WHERE host=? AND id=? AND expires<=?)
              AND ((SELECT count(*) FROM request_images WHERE host=?)<? OR EXISTS(SELECT 1 FROM request_images WHERE host=? AND id=?))
              AND (SELECT COALESCE(sum(bytes),0) FROM request_images WHERE host=? AND id!=?)+?<=?
              ON CONFLICT(host,id) DO UPDATE SET revision=excluded.revision,digest=excluded.digest,payload=excluded.payload,bytes=excluded.bytes,mime=excluded.mime,width=excluded.width,height=excluded.height,orphan_until=COALESCE(request_images.orphan_until,excluded.orphan_until)
              WHERE request_images.revision=? AND request_images.digest=? AND request_images.request_id=excluded.request_id AND request_images.created=excluded.created AND request_images.expires=excluded.expires RETURNING revision`)
              .bind(host,record,p.request,b.revision,b.digest,b.payload,size,p.mime,p.width,p.height,p.created,p.expires,now+3600,host,tokenHash,host,record,p.request,p.created,p.expires,now,host,p.request,now,host,imageRows,host,record,host,record,size,imageHostBytes,old?.revision??0,old?.digest??'')
          ]);
          return result[3].results.length?json({ok:true,revision:b.revision,digest:b.digest}):json({error:'IMAGE_CAPACITY'},409);
        }
        const part=Number(url.searchParams.get('part')??0);if(!Number.isSafeInteger(part)||part<0||part>=24)return json({error:'INVALID'},400);
        const row=await db.prepare(`SELECT i.revision,i.digest,i.bytes,hex(substr(CAST(i.payload AS BLOB),?,?)) AS chunk FROM request_images i
          WHERE i.host=? AND i.id=? AND i.expires>? AND EXISTS(SELECT 1 FROM request_details d,json_each(d.payload,'$.images') ref
            WHERE d.host=i.host AND d.id=i.request_id AND d.expires>? AND json_extract(ref.value,'$.id')=i.id
            AND json_extract(ref.value,'$.availability')='available' AND json_extract(ref.value,'$.expires')=i.expires
            AND json_extract(ref.value,'$.mime')=i.mime AND json_extract(ref.value,'$.width')=i.width AND json_extract(ref.value,'$.height')=i.height)`)
          .bind(part*chunkBytes+1,chunkBytes,host,record,now,now).first();
        if(!row) {const expired=await db.prepare('SELECT expires FROM image_lifetimes WHERE host=? AND id=? AND expires<=?').bind(host,record,now).first();return json({error:expired?'IMAGE_EXPIRED':'IMAGE_UNAVAILABLE'},expired?410:404);}
        const count=Math.ceil(row.bytes/chunkBytes);if(part>=count)return json({error:'INVALID'},400);
        return json({action:'image',imageID:record,imagePart:part,imageManifest:{id:record,revision:row.revision,digest:row.digest,bytes:row.bytes,parts:count},imageChunk:base64Hex(row.chunk)});
      }
      if(parts[3]==='details') {
        if(!(await hasDetails(env)))return json({error:'DETAILS_UNSUPPORTED'},404);
        const record=parts[4];
        if(request.method==='PUT') {
          const b=await body(request,2*detailLimit+2048), incomingSize=typeof b.payload==='string'?encoder.encode(b.payload).length:0;
          if(b.dataset!=='detail-'+record||!Number.isSafeInteger(b.revision)||b.revision<1||incomingSize<1||incomingSize>detailLimit||await digest(b.payload)!==b.digest)return json({error:'INVALID'},400);
          const p=JSON.parse(b.payload);if(!validDetail(p,record,now))return json({error:'INVALID'},400);
          if(p.images!=null && !(await hasImages(env)))return json({error:'IMAGES_UNSUPPORTED'},404);
          await retainDetailIfDue(db,host,record,now,tokenHash);
          const old=await db.prepare("SELECT d.revision,d.digest,d.started,d.expires,r.source_revision,r.source_digest FROM request_details d LEFT JOIN payload_retention r ON r.host=d.host AND r.kind='detail' AND r.name=d.id WHERE d.host=? AND d.id=?").bind(host,record).first();
          if(old?.expires<=now)return json({error:'DETAIL_EXPIRED'},410);
          if(old && old.started!==p.started)return json({error:'DETAIL_ID_CONFLICT'},409);
          if(old && (old.source_revision??old.revision)===b.revision && (old.source_digest??old.digest)===b.digest)return json({ok:true,revision:old.revision,digest:old.digest});
          if(old && (old.source_revision??old.revision)>=b.revision)return json({error:'REVISION_CONFLICT',revision:old.revision,digest:old.digest},409);
          const retained=retainPayload('detail',b.payload,now), hash=retained.payload===b.payload?b.digest:await digest(retained.payload), size=encoder.encode(retained.payload).length;
          const revision=Math.max(b.revision+(hash===b.digest?0:1),(old?.revision??0)+1), expires=Math.floor(Math.max(p.started,p.completed??p.started)+retention);
          if(!Number.isSafeInteger(revision))return json({error:'INVALID'},400);
          // Each host contains <=64 details. Admission and the single permitted
          // older-record eviction remain in one transaction; triggers remove its
          // image bytes and retention job, while lifetime metadata prevents renewal.
          const result=await db.batch([
            db.prepare(`DELETE FROM request_details WHERE host=? AND id=(SELECT id FROM request_details WHERE host=? AND id!=? ORDER BY started,id LIMIT 1)
              AND started<=? AND EXISTS(SELECT 1 FROM hosts WHERE id=? AND writer_hash=?)
              AND ((SELECT count(*) FROM request_details WHERE host=?)>=? OR (SELECT COALESCE(sum(bytes),0) FROM request_details WHERE host=? AND id!=?)+?>?)
              AND (SELECT COALESCE(sum(bytes),0) FROM request_details WHERE host=? AND id!=?)-bytes+?<=?
              AND NOT EXISTS(SELECT 1 FROM request_details WHERE host=? AND id=?)`).bind(host,host,record,p.started,host,tokenHash,host,detailRows,host,record,size,hostBytes,host,record,size,hostBytes,host,record),
            db.prepare(`INSERT INTO request_details(host,id,revision,digest,payload,bytes,started,expires) SELECT ?,?,?,?,?,?,?,?
              WHERE EXISTS(SELECT 1 FROM hosts WHERE id=? AND writer_hash=?)
              AND ((SELECT count(*) FROM request_details WHERE host=?)<? OR EXISTS(SELECT 1 FROM request_details WHERE host=? AND id=?))
              AND (SELECT COALESCE(sum(bytes),0) FROM request_details WHERE host=? AND id!=?)+?<=?
              ON CONFLICT(host,id) DO UPDATE SET revision=excluded.revision,digest=excluded.digest,payload=excluded.payload,bytes=excluded.bytes,expires=excluded.expires
              WHERE request_details.revision=? AND request_details.digest=? AND request_details.started=excluded.started AND request_details.expires>? RETURNING revision`)
              .bind(host,record,revision,hash,retained.payload,size,p.started,expires,host,tokenHash,host,detailRows,host,record,host,record,size,hostBytes,old?.revision??0,old?.digest??'',now),
            retentionStatement(db,host,'detail',record,b.revision,b.digest,retained.due,revision,hash,tokenHash)
          ]);
          return result[1].results.length?json({ok:true,revision,digest:hash}):json({error:'DETAIL_CAPACITY'},409);
        }
        await retainDetailIfDue(db,host,record,now);
        const full=url.searchParams.get('full')==='1', part=Number(url.searchParams.get('part')??0);
        if(!Number.isSafeInteger(part)||part<0||part>=16)return json({error:'INVALID'},400);
        const row=await db.prepare(full
          ?'SELECT revision,digest,bytes,expires,json_extract(payload,\'$.availability\') AS availability,hex(substr(CAST(payload AS BLOB),?,?)) AS chunk FROM request_details WHERE host=? AND id=?'
          :'SELECT revision,digest,bytes,expires,payload FROM request_details WHERE host=? AND id=?')
          .bind(...(full?[part*chunkBytes+1,chunkBytes,host,record]:[host,record])).first();
        if(!row)return json({error:'DETAIL_UNAVAILABLE'},404);
        if(row.expires<=now)return json({error:'DETAIL_EXPIRED'},410);
        if(full) {
          if(row.availability==='capacity')return json({error:'DETAIL_CAPACITY'},413);
          const count=Math.ceil(row.bytes/chunkBytes);if(part>=count)return json({error:'INVALID'},400);
          return json({action:'detail',detailID:record,full:true,detailPart:part,detailManifest:{id:record,revision:row.revision,digest:row.digest,bytes:row.bytes,parts:count},detailChunk:base64Hex(row.chunk)});
        }
        const p=JSON.parse(row.payload);p.user=prefix(p.user,1200);p.final=prefix(p.final,5000);p.full=false;p.attachments=p.attachments.map(({thumbnail,...item})=>item);
        const payload=JSON.stringify(p);
        return json({action:'detail',detailID:record,full:false,detail:{dataset:'detail-'+record,revision:row.revision,digest:await digest(payload),payload}});
      }
      if(parts[3]==='sync') {
        if(!(await storage(env)).has('payload_retention'))return json({error:'RETENTION_UNSUPPORTED'},503);
        const rows=await db.prepare("SELECT d.*,r.source_revision,r.source_digest,r.due FROM datasets d LEFT JOIN payload_retention r ON r.host=d.host AND r.kind='dataset' AND r.name=d.dataset WHERE d.host=? AND d.updated>? LIMIT 3").bind(host,now-retention).all();
        const retained=[];
        for(const row of rows.results)retained.push(row.source_revision==null || row.due!=null && row.due<=now?await retainRow(db,'dataset',row,now):row);
        const supported=await hasDetails(env);
        // Ref expiry and legacy thumbnail removal change detail revisions before
        // readers decide whether an already downloaded detail is still current.
        if(supported) {
          const due=await db.prepare("SELECT name FROM payload_retention WHERE host=? AND kind='detail' AND due<=? LIMIT 64").bind(host,now).all();
          // /sync remains bounded and does not fetch 64 full detail bodies.
          // Clients enforce image timestamps immediately; scheduled cleanup and
          // the first detail read canonicalize bodies whose jobs are still due.
          if(due.results.length) for(const item of due.results.slice(0,2))await retainDetailIfDue(db,host,item.name,now);
        }
        const versions=supported?await db.prepare('SELECT id,revision FROM request_details WHERE host=? AND expires>? LIMIT 64').bind(host,now).all():{results:[]};
        return json({action:'sync',capabilities:await capabilities(env),detailVersions:Object.fromEntries(versions.results.map(row=>[row.id,row.revision])),seen:Math.max(0,...retained.map(row=>row.updated)),datasets:retained.filter(row=>row.revision>Number(url.searchParams.get(row.dataset)??0)).map(({dataset,revision,digest,payload})=>({dataset,revision,digest,payload}))});
      }
      return json({error:'NOT_FOUND'},404);
    } catch { return json({error:'SYNC_UNAVAILABLE'},503); }
  },
  async scheduled(_event,env) {
    const db=env.DB, now=Math.floor(Date.now()/1000), day=new Date(Date.now()-8*86400000).toISOString().slice(0,10), tables=await storage(env);
    // Maintenance runs even while feature flags are disabled. Every selection
    // uses an expiry index or a bounded host/record primary-key lookup.
    await db.batch([
      db.prepare('DELETE FROM budget WHERE day IN (SELECT day FROM budget WHERE day<? LIMIT 32)').bind(day),
      db.prepare('DELETE FROM invites WHERE hash IN (SELECT hash FROM invites WHERE expires<? LIMIT 512)').bind(now-86400),
      db.prepare('DELETE FROM datasets WHERE (host,dataset) IN (SELECT host,dataset FROM datasets WHERE updated<=? ORDER BY updated LIMIT 512)').bind(now-retention)
    ]);
    for(const table of ['request_images','request_details','image_lifetimes']) if(tables.has(table)) {
      const expiry=table==='image_lifetimes'?'forget':'expires';
      for(let batch=0;batch<2;batch++) {
        const result=await db.prepare(`DELETE FROM ${table} WHERE (host,id) IN (SELECT host,id FROM ${table} WHERE ${expiry}<=? ORDER BY ${expiry} LIMIT 512)`).bind(now).run();
        if((result.meta?.changes??0)<512)break;
      }
    }
    if(tables.has('request_images')) {
      // The first hour permits either upload order. Confirmed references clear
      // the job; absent/removed references cause physical deletion of the bytes.
      await db.batch([
        db.prepare(`UPDATE request_images SET orphan_until=NULL WHERE (host,id) IN (SELECT host,id FROM request_images WHERE orphan_until<=? ORDER BY orphan_until LIMIT 512)
          AND EXISTS(SELECT 1 FROM request_details d,json_each(d.payload,'$.images') ref WHERE d.host=request_images.host AND d.id=request_images.request_id AND d.expires>?
            AND json_extract(ref.value,'$.id')=request_images.id AND json_extract(ref.value,'$.availability')='available'
            AND json_extract(ref.value,'$.expires')=request_images.expires AND json_extract(ref.value,'$.mime')=request_images.mime
            AND json_extract(ref.value,'$.width')=request_images.width AND json_extract(ref.value,'$.height')=request_images.height)`).bind(now,now),
        db.prepare(`DELETE FROM request_images WHERE (host,id) IN (SELECT host,id FROM request_images WHERE orphan_until<=? ORDER BY orphan_until LIMIT 512)
          AND NOT EXISTS(SELECT 1 FROM request_details d,json_each(d.payload,'$.images') ref WHERE d.host=request_images.host AND d.id=request_images.request_id AND d.expires>?
            AND json_extract(ref.value,'$.id')=request_images.id AND json_extract(ref.value,'$.availability')='available'
            AND json_extract(ref.value,'$.expires')=request_images.expires AND json_extract(ref.value,'$.mime')=request_images.mime
            AND json_extract(ref.value,'$.width')=request_images.width AND json_extract(ref.value,'$.height')=request_images.height)`).bind(now,now)
      ]);
    }
    if(tables.has('payload_retention') && tables.has('request_details')) for(let batch=0;batch<2;batch++) {
      const jobs=await db.prepare(`SELECT r.host,r.kind,r.name,r.source_revision,r.source_digest,d.id,s.dataset,
          COALESCE(d.payload,s.payload) AS payload,COALESCE(d.revision,s.revision) AS revision,COALESCE(d.digest,s.digest) AS digest
        FROM payload_retention r LEFT JOIN request_details d ON r.kind='detail' AND d.host=r.host AND d.id=r.name
        LEFT JOIN datasets s ON r.kind='dataset' AND s.host=r.host AND s.dataset=r.name WHERE r.due<=? ORDER BY r.due LIMIT 8`).bind(now).all();
      for(const row of jobs.results) {
        if(row.payload!=null)await retainRow(db,row.kind,row,now);
        else await db.prepare('DELETE FROM payload_retention WHERE host=? AND kind=? AND name=?').bind(row.host,row.kind,row.name).run();
      }
      if(jobs.results.length<8)break;
    }
  }
};
