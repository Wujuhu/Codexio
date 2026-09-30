<script lang="ts">
 import {marked} from 'marked'; import DOMPurify from 'dompurify'; import {openURL} from '../lib/api';
 export let content=''; export let onerror:(e:any)=>void=()=>{};
 $: safe=DOMPurify.sanitize(marked.parse(String(content),{async:false}) as string,{USE_PROFILES:{html:true},FORBID_TAGS:['img','iframe','style','form','input','button'],FORBID_ATTR:['style','id']});
 function click(event:MouseEvent){const anchor=(event.target as HTMLElement).closest('a');if(anchor){event.preventDefault();void openURL(anchor.getAttribute('href')??'').catch(onerror);}}
 function links(node:HTMLElement){node.addEventListener('click',click);return{destroy(){node.removeEventListener('click',click)}}}
</script>
<div class="markdown" use:links role="document">{@html safe}</div>
