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
const detailLimit = 1048576, chunkBytes = 65536, detailRows = 64, hostBytes = 8 * 1048576, retention = 7 * 86400;
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
let detailSchema = null;
async function hasDetails(env) {
  if (env.DETAILS_ENABLED !== '1') return false;
  if (!detailSchema || detailSchema.db !== env.DB || detailSchema.until <= Date.now()) {
    const state = {db:env.DB,until:Date.now()+300000,promise:null};
    state.promise = env.DB.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='request_details'").first().then(row=>!!row).catch(()=>false);
    detailSchema = state;
  }
  return detailSchema.promise;
}
function validDetail(value, record, now) {
  const keys = new Set(['id','started','completed','status','user','final','userComplete','finalComplete','availability','attachments','full']);
  if (!value || typeof value !== 'object' || Object.keys(value).some(key=>!keys.has(key)) || value.id !== record) return false;
  if (!Number.isFinite(value.started) || value.started <= 0 || value.started > now+300 || value.completed != null && (!Number.isFinite(value.completed) || value.completed < value.started || value.completed > now+300)) return false;
  if (Math.max(value.started,value.completed ?? value.started) < now-retention) return false;
  if (!['running','completed','aborted','failed','unknown',''].includes(value.status) || !['available','partial','unavailable','capacity'].includes(value.availability)) return false;
  if (typeof value.user !== 'string' || typeof value.final !== 'string' || typeof value.userComplete !== 'boolean' || typeof value.finalComplete !== 'boolean' || typeof value.full !== 'boolean') return false;
  if (!Array.isArray(value.attachments) || value.attachments.length > 6) return false;
  let images = 0;
  return value.attachments.every(item=>{
    if (!item || Object.keys(item).some(key=>!['id','name','mime','thumbnail'].includes(key)) || !requestID(item.id) || typeof item.name !== 'string' || encoder.encode(item.name).length > 240 || item.mime != null && (typeof item.mime !== 'string' || item.mime.length > 100)) return false;
    if (item.thumbnail == null) return true;
    if (++images > 2 || typeof item.thumbnail !== 'string' || item.thumbnail.length > 16384 || !/^[A-Za-z0-9+/]+={0,2}$/.test(item.thumbnail)) return false;
    try { const bytes=atob(item.thumbnail); return bytes.length <= 12288 && bytes.charCodeAt(0)===255 && bytes.charCodeAt(1)===216; } catch { return false; }
  });
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
      const readerRoute = request.method==='GET' && ((parts[3]==='sync' && parts.length===4) || (parts[3]==='details' && parts.length===5 && requestID(parts[4])));
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
        (request.method==='PUT' && parts.length===5 && ((parts[3]==='data' && datasets.includes(parts[4])) || (parts[3]==='details' && requestID(parts[4]))));
      if(!route)return json({error:'NOT_FOUND'},404);
      if(!(await admit(env)))return json({error:'DAILY_BUDGET'},429);
      if(parts[3]==='capabilities') return json({action:'capabilities',capabilities:[requestKindCapability,...(await hasDetails(env)?[capability]:[])]});
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
        const b=await body(request), dataset=parts[4];
        if(b.dataset!==dataset||!Number.isSafeInteger(b.revision)||b.revision<1||typeof b.payload!=='string'||encoder.encode(b.payload).length>({live:8000,recent:120000,trends:80000}[dataset])||await digest(b.payload)!==b.digest)return json({error:'INVALID'},400);
        const p=JSON.parse(b.payload);if(!validPayload(p)||dataset==='recent'&&!Array.isArray(p)||dataset==='trends'&&(!Array.isArray(p.daily)||p.daily.length>90||!Array.isArray(p.periods)||p.periods.length>3)||dataset==='live'&&(!p.today||!p.five||!p.week))return json({error:'INVALID'},400);
        const result=await db.prepare('INSERT INTO datasets(host,dataset,revision,digest,payload,updated) SELECT ?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM hosts WHERE id=? AND writer_hash=?) ON CONFLICT(host,dataset) DO UPDATE SET revision=excluded.revision,digest=excluded.digest,payload=excluded.payload,updated=excluded.updated WHERE excluded.revision>datasets.revision RETURNING revision').bind(host,dataset,b.revision,b.digest,b.payload,now,host,tokenHash).first();
        if(!result){const old=await db.prepare('SELECT revision,digest FROM datasets WHERE host=? AND dataset=?').bind(host,dataset).first();if(old?.revision!==b.revision||old?.digest!==b.digest)return json({error:'REVISION_CONFLICT'},409);}
        return json({ok:true});
      }
      if(parts[3]==='details') {
        if(!(await hasDetails(env)))return json({error:'DETAILS_UNSUPPORTED'},404);
        const record=parts[4];
        if(request.method==='PUT') {
          // One bounded upload only when content changes. Reader downloads are
          // 64 KiB chunks, never a whole-history response or a body in /sync.
          const b=await body(request,2*detailLimit+2048), size=typeof b.payload==='string'?encoder.encode(b.payload).length:0;
          if(b.dataset!=='detail-'+record||!Number.isSafeInteger(b.revision)||b.revision<1||size<1||size>detailLimit||await digest(b.payload)!==b.digest)return json({error:'INVALID'},400);
          const p=JSON.parse(b.payload);if(!validDetail(p,record,now))return json({error:'INVALID'},400);
          const old=await db.prepare('SELECT revision,digest FROM request_details WHERE host=? AND id=?').bind(host,record).first();
          if(old?.revision===b.revision && old?.digest===b.digest)return json({ok:true});
          if(old && old.revision>=b.revision)return json({error:'REVISION_CONFLICT'},409);
          const expires=Math.floor(Math.max(p.started,p.completed ?? p.started)+retention);
          // Transactional count/byte admission. At most one older record may be
          // evicted per upload; otherwise capacity is reported honestly. The
          // host PK limits every aggregate and sort to <=64 records.
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
              ON CONFLICT(host,id) DO UPDATE SET revision=excluded.revision,digest=excluded.digest,payload=excluded.payload,bytes=excluded.bytes,started=excluded.started,expires=excluded.expires WHERE excluded.revision>request_details.revision RETURNING revision`).bind(host,record,b.revision,b.digest,b.payload,size,p.started,expires,host,tokenHash,host,detailRows,host,record,host,record,size,hostBytes)
          ]);
          return result[1].results.length?json({ok:true}):json({error:'DETAIL_CAPACITY'},409);
        }
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
        const rows=await db.prepare('SELECT dataset,revision,digest,payload,updated FROM datasets WHERE host=? AND updated>? LIMIT 3').bind(host,now-retention).all();
        const supported=await hasDetails(env);
        const versions=supported?await db.prepare('SELECT id,revision FROM request_details WHERE host=? AND expires>? LIMIT 64').bind(host,now).all():{results:[]};
        return json({action:'sync',capabilities:[requestKindCapability,...(supported?[capability]:[])],detailVersions:Object.fromEntries(versions.results.map(row=>[row.id,row.revision])),seen:Math.max(0,...rows.results.map(x=>x.updated)),datasets:rows.results.filter(x=>x.revision>Number(url.searchParams.get(x.dataset)??0)).map(({updated,...x})=>x)});
      }
      return json({error:'NOT_FOUND'},404);
    } catch { return json({error:'SYNC_UNAVAILABLE'},503); }
  },
  async scheduled(_event,env) {
    const now=Math.floor(Date.now()/1000), day=new Date(Date.now()-8*86400000).toISOString().slice(0,10);
    // Bounded, indexed expiry cleanup; unused reservations are not refunded.
    await env.DB.batch([
      env.DB.prepare('DELETE FROM budget WHERE day IN (SELECT day FROM budget WHERE day<? LIMIT 32)').bind(day),
      env.DB.prepare('DELETE FROM invites WHERE hash IN (SELECT hash FROM invites WHERE expires<? LIMIT 512)').bind(now-86400),
      env.DB.prepare('DELETE FROM datasets WHERE (host,dataset) IN (SELECT host,dataset FROM datasets WHERE updated<? LIMIT 512)').bind(now-retention)
    ]);
    if(await hasDetails(env)) for(let batch=0;batch<4;batch++) {
      const result=await env.DB.prepare('DELETE FROM request_details WHERE (host,id) IN (SELECT host,id FROM request_details WHERE expires<=? LIMIT 512)').bind(now).run();
      if((result.meta?.changes??0)<512)break;
    }
  }
};
