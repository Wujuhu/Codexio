<script lang="ts">
 export let primary='';
 export let secondary='';
 export let secondaryTitle='';
</script>
<span class="request-lines"><span class="primary" title={primary}>{primary}</span><span class="secondary" title={secondaryTitle||secondary}>{secondary}</span></span>
<style>
 .request-lines{display:block;min-width:0;max-width:100%;text-align:inherit}
 .primary,.secondary{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin:0;padding:0;font-family:inherit;font-weight:400}
 .primary{font-size:inherit;line-height:18px}
 .secondary{font-size:9px;line-height:12px;color:var(--muted)}
</style>
