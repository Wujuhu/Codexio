<script lang="ts">
  import {mount,unmount} from 'svelte';
  import {marked,Renderer} from 'marked';
  import DOMPurify from 'dompurify';
  import {openURL,type Row} from '../lib/api';
  import {tr} from '../lib/i18n';
  import RequestImage from './RequestImage.svelte';
  export let content='';export let requestId='';export let images:Row[]=[];export let onerror:(error:any)=>void=()=>{};
  const renderer=new Renderer();
  renderer.image=({href,text})=>{
    const id=/^codexio-image:\/\/([a-f0-9]{64})$/.exec(href)?.[1];
    const label=String(text).replace(/[&<>"']/g,value=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[value]!));
    return id?`<span data-request-image="${id}">${label}</span>`:`<span class="unavailable-image">${label} · ${tr('暂不可用')}</span>`;
  };
  $: safe=DOMPurify.sanitize(marked.parse(String(content),{async:false,renderer}) as string,{USE_PROFILES:{html:true},FORBID_TAGS:['img','iframe','style','form','input','button','video','audio','source','object','embed'],FORBID_ATTR:['style','id','src','srcset']});
  function render(node:HTMLElement,value:{safe:string;requestId:string;images:Row[]}){
    let mounted:ReturnType<typeof mount>[]=[];
    let renderedHTML='',renderedRequest='',renderedImages='';
    function clear(){for(const component of mounted)void unmount(component);mounted=[]}
    function update(next:typeof value){
      const signature=JSON.stringify(next.images);
      if(renderedHTML===next.safe&&renderedRequest===next.requestId&&renderedImages===signature)return;
      renderedHTML=next.safe;renderedRequest=next.requestId;renderedImages=signature;
      clear();node.innerHTML=next.safe;
      for(const table of node.querySelectorAll('table')){const scroll=document.createElement('div');scroll.className='markdown-table-scroll';scroll.tabIndex=0;table.replaceWith(scroll);scroll.append(table)}
      const seen=new Set<string>(),contentDigests=new Set<string>();
      for(const placeholder of node.querySelectorAll<HTMLElement>('[data-request-image]')){
        const id=placeholder.dataset.requestImage??'',image=next.images.find(image=>image.id===id);
        if(!image||!next.requestId){placeholder.textContent=`${placeholder.textContent} · ${tr('暂不可用')}`;continue}
        if(seen.has(id)){placeholder.remove();continue}
        seen.add(id);placeholder.replaceChildren();
        mounted.push(mount(RequestImage,{target:placeholder,props:{requestId:next.requestId,image,claim:(digest:string)=>{if(contentDigests.has(digest))return false;contentDigests.add(digest);return true}}}));
      }
    }
    function click(event:MouseEvent){const anchor=(event.target as HTMLElement).closest('a');if(anchor){event.preventDefault();void openURL(anchor.getAttribute('href')??'').catch(onerror)}}
    node.addEventListener('click',click);update(value);
    return{update,destroy(){node.removeEventListener('click',click);clear()}};
  }
</script>
<div class="markdown" use:render={{safe,requestId,images}} role="document"></div>
<style>
  .markdown{font-size:12px;line-height:1.65;text-align:left;overflow-wrap:anywhere;white-space:normal;min-width:0}.markdown :global(p){font-size:12px;line-height:1.65;text-align:left;margin:7px 0}.markdown :global(h1),.markdown :global(h2),.markdown :global(h3),.markdown :global(h4),.markdown :global(h5),.markdown :global(h6){font-size:13px;font-weight:600;text-align:left;line-height:1.5;margin:12px 0 6px;padding:0}.markdown :global(ul),.markdown :global(ol){padding-left:20px;margin:7px 0;text-align:left}.markdown :global(li){margin:3px 0}.markdown :global(pre){background:color-mix(in srgb,var(--bg) 70%,transparent);padding:10px;border-radius:8px;overflow:auto;text-align:left;font-size:11px;line-height:1.6;white-space:pre}.markdown :global(code){font-family:Consolas,monospace;font-size:11px}.markdown :global(.markdown-table-scroll){display:block;max-width:100%;overflow-x:auto;margin:8px 0}.markdown :global(table){min-width:100%;width:max-content;table-layout:auto;font-size:11px}.markdown :global(th),.markdown :global(td){position:static;height:auto;min-width:100px;max-width:260px;padding:6px;text-align:left;vertical-align:top;white-space:normal;overflow-wrap:anywhere;font-size:11px;line-height:1.6}.markdown :global(blockquote){margin:8px 0;border-left:2px solid var(--border);padding-left:10px;color:var(--muted);text-align:left}.markdown :global(hr){border:0;border-top:1px solid var(--border);margin:12px 0}.markdown :global(a){word-break:break-word}.markdown :global(.unavailable-image){display:inline-block;color:var(--muted);font-size:11px}
</style>
