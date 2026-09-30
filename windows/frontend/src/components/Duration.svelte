<script lang="ts">
 import{onDestroy}from'svelte';import{number}from'../lib/api';export let milliseconds:any=null;export let running=false;let elapsed:number|null=null;let timer:ReturnType<typeof setInterval>|undefined;
 $: configure(milliseconds,running);
 function configure(value:any,active:boolean){if(timer)clearInterval(timer);timer=undefined;const base=number(value);elapsed=base;if(active&&base!==null){const began=performance.now();timer=setInterval(()=>elapsed=base+performance.now()-began,1000)}}
 onDestroy(()=>{if(timer)clearInterval(timer)});
</script>
{elapsed===null?'—':(elapsed/1000).toFixed(1)+'s'}
