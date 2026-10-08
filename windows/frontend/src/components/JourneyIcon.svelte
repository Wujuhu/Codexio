<script lang="ts">
 import {journeyIcon} from './journey';
 export let kind='default';export let size=16;
</script>
<span class="journey-icon" aria-hidden="true" style:width={`${size}px`} style:height={`${size}px`} style:mask-image={`url('/journey-icons/${journeyIcon(kind)}.svg')`}></span>
<style>
 .journey-icon{display:inline-block;flex:none;background:currentColor;mask-position:center;mask-repeat:no-repeat;mask-size:contain;vertical-align:middle}
</style>
