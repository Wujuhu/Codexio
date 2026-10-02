import {Call} from '@wailsio/runtime';
import {type Row} from '../lib/api';

type Subscriber={resolve:(value:Row)=>void;reject:(error:unknown)=>void;signal:AbortSignal;abort:()=>void};
type ImageJob={key:string;requestId:string;imageId:string;subscribers:Set<Subscriber>;started:boolean;finished:boolean;pending?:Promise<Row>&{cancel?:()=>void}};

// This wrapper owns sharing and cancellation; bypass generic api() read dedup
// so a reopened inspector cannot inherit an already-cancelled promise.
let active=0;
const jobs=new Map<string,ImageJob>();
const queue:ImageJob[]=[];
const runningKeys=new Set<string>();
const cancelled=()=>new DOMException('Image request cancelled','AbortError');

function finish(job:ImageJob,value:Row|undefined,error?:unknown){
  if(job.finished)return;
  job.finished=true;
  active--;runningKeys.delete(job.key);
  if(jobs.get(job.key)===job)jobs.delete(job.key);
  const subscribers=[...job.subscribers];
  job.subscribers.clear();job.pending=undefined;
  for(const subscriber of subscribers){
    subscriber.signal.removeEventListener('abort',subscriber.abort);
    if(subscriber.signal.aborted)subscriber.reject(cancelled());
    else if(value!==undefined)subscriber.resolve(value);
    else subscriber.reject(error);
  }
  advance();
}

function advance(){
  while(active<2){
    // A replacement for an abandoned active job waits for that job to settle;
    // its old completion must not remove or notify the replacement's users.
    const index=queue.findIndex(job=>job.subscribers.size>0&&!runningKeys.has(job.key));
    if(index<0)return;
    const job=queue.splice(index,1)[0];
    job.started=true;active++;runningKeys.add(job.key);
    try{
      job.pending=Call.ByName('codexio/windows/backend.Service.GetRequestImage',job.requestId,job.imageId) as Promise<Row>&{cancel?:()=>void};
      void job.pending.then(value=>finish(job,value),error=>finish(job,undefined,error));
    }catch(error){finish(job,undefined,error)}
  }
}

export function loadRequestImage(requestId:string,imageId:string,signal:AbortSignal):Promise<Row>{
  return new Promise((resolve,reject)=>{
    if(signal.aborted){reject(cancelled());return}
    const key=JSON.stringify([requestId,imageId]);
    let job=jobs.get(key);
    if(!job){
      job={key,requestId,imageId,subscribers:new Set(),started:false,finished:false};
      jobs.set(key,job);queue.push(job);
    }
    const selected=job;
    const subscriber:Subscriber={resolve,reject,signal,abort:()=>{
      if(!selected.subscribers.delete(subscriber))return;
      signal.removeEventListener('abort',subscriber.abort);
      reject(cancelled());
      if(selected.subscribers.size===0){
        if(jobs.get(key)===selected)jobs.delete(key);
        if(selected.started){
          // Keep its active slot until the underlying request actually settles.
          try{selected.pending?.cancel?.()}catch{/* The bounded call still owns its slot. */}
        }else{
          const index=queue.indexOf(selected);
          if(index>=0)queue.splice(index,1);
        }
      }
      advance();
    }};
    selected.subscribers.add(subscriber);
    signal.addEventListener('abort',subscriber.abort,{once:true});
    advance();
  });
}
