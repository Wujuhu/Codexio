<script lang="ts">
  import {marked} from 'marked';
  import DOMPurify from 'dompurify';
  import {openURL} from '../lib/api';
  export let content=''; export let onerror:(error:any)=>void=()=>{};
  $: safe=DOMPurify.sanitize(marked.parse(String(content),{async:false}) as string,{USE_PROFILES:{html:true},FORBID_TAGS:['img','iframe','style','form','input','button'],FORBID_ATTR:['style','id']});
  function click(event:MouseEvent){const anchor=(event.target as HTMLElement).closest('a');if(anchor){event.preventDefault();void openURL(anchor.getAttribute('href')??'').catch(onerror)}}
  function links(node:HTMLElement){node.addEventListener('click',click);return{destroy(){node.removeEventListener('click',click)}}}
</script>
<div class="markdown" use:links role="document">{@html safe}</div>
<style>
  .markdown{font-size:12px;line-height:1.65;text-align:left;overflow-wrap:anywhere;white-space:normal}.markdown :global(p){font-size:12px;line-height:1.65;text-align:left;margin:7px 0}.markdown :global(h1),.markdown :global(h2),.markdown :global(h3),.markdown :global(h4),.markdown :global(h5),.markdown :global(h6){font-size:13px;font-weight:600;text-align:left;line-height:1.5;margin:12px 0 6px;padding:0}.markdown :global(ul),.markdown :global(ol){padding-left:20px;margin:7px 0;text-align:left}.markdown :global(li){margin:3px 0}.markdown :global(pre){background:color-mix(in srgb,var(--bg) 70%,transparent);padding:10px;border-radius:8px;overflow:auto;text-align:left;font-size:11px;line-height:1.6;white-space:pre}.markdown :global(code){font-family:Consolas,monospace;font-size:11px}.markdown :global(table){min-width:0;width:100%;table-layout:auto;font-size:11px}.markdown :global(th),.markdown :global(td){padding:5px;text-align:left;white-space:normal;overflow-wrap:anywhere;font-size:11px}.markdown :global(blockquote){margin:8px 0;border-left:2px solid var(--border);padding-left:10px;color:var(--muted);text-align:left}.markdown :global(hr){border:0;border-top:1px solid var(--border);margin:12px 0}.markdown :global(a){word-break:break-word}
</style>
