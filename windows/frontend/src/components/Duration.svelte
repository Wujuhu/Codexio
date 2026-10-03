<script context="module" lang="ts">
 const clocks=new Set<(stamp:number)=>void>();
 let sharedTimer:ReturnType<typeof setInterval>|undefined;
 function subscribeClock(listener:(stamp:number)=>void){
  clocks.add(listener);
  if(sharedTimer===undefined)sharedTimer=setInterval(()=>{const stamp=Date.now();for(const tick of clocks)tick(stamp)},1000);
  return()=>{clocks.delete(listener);if(!clocks.size&&sharedTimer!==undefined){clearInterval(sharedTimer);sharedTimer=undefined}};
 }
</script>
<script lang="ts">
 import{onMount}from'svelte';
 import{type Row}from'../lib/api';
 import{duration,durationMilliseconds,runningDuration}from'./logFormat';
 export let record:Row={};
 let element:HTMLSpanElement;let now=Date.now();let visible=false;let foreground=false;
 let unsubscribe:(()=>void)|undefined;
 $: active=runningDuration(record);
 $: elapsed=durationMilliseconds(record,now);
 $: configure(active&&visible&&foreground);
 function stop(){unsubscribe?.();unsubscribe=undefined}
 function configure(ticking:boolean){
  if(!ticking){stop();return}
  if(unsubscribe)return;
  now=Date.now();
  unsubscribe=subscribeClock(stamp=>{now=stamp;if(!runningDuration(record))stop()});
 }
 onMount(()=>{
  const visibility=()=>{foreground=!document.hidden&&document.hasFocus();now=Date.now()};
  visibility();
  document.addEventListener('visibilitychange',visibility);
  window.addEventListener('focus',visibility);window.addEventListener('blur',visibility);
  const observer=new IntersectionObserver(entries=>{visible=entries[0]?.isIntersecting??false;if(visible)now=Date.now()});
  observer.observe(element);
  return()=>{observer.disconnect();document.removeEventListener('visibilitychange',visibility);window.removeEventListener('focus',visibility);window.removeEventListener('blur',visibility);stop()};
 });
</script>
<span bind:this={element}>{duration(elapsed)}</span>
