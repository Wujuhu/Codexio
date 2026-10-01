<script lang="ts">
 import{onMount}from'svelte';
 import{type Row}from'../lib/api';
 import{duration,durationMilliseconds,runningDuration}from'./logFormat';
 export let record:Row={};
 let element:HTMLSpanElement;let now=Date.now();let visible=false;let foreground=false;
 let timer:ReturnType<typeof setInterval>|undefined;
 $: active=runningDuration(record);
 $: elapsed=durationMilliseconds(record,now);
 $: configure(active&&visible&&foreground);
 function stop(){if(timer!==undefined){clearInterval(timer);timer=undefined}}
 function configure(ticking:boolean){
  if(!ticking){stop();return}
  if(timer!==undefined)return;
  now=Date.now();
  timer=setInterval(()=>{now=Date.now();if(!runningDuration(record,now))stop()},1000);
 }
 onMount(()=>{
  const visibility=()=>{foreground=!document.hidden;now=Date.now()};
  visibility();
  document.addEventListener('visibilitychange',visibility);
  const observer=new IntersectionObserver(entries=>{visible=entries[0]?.isIntersecting??false;if(visible)now=Date.now()});
  observer.observe(element);
  return()=>{observer.disconnect();document.removeEventListener('visibilitychange',visibility);stop()};
 });
</script>
<span bind:this={element}>{duration(elapsed)}</span>
