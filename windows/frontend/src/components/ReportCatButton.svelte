<script lang="ts">
  import {onMount} from 'svelte';

  export let onopen: () => void;
  export let suspended = false;
  export let dark = false;
  export let label = '打开 AI 使用报告';

  // Keep the canvas, placements and anchors from macos/native/ReportCatAnimation.swift.
  const unit = 0.18;
  const placements = [
    {name: 'tail', x: 79, y: 83, width: 43, aspect: 280 / 300, anchorX: 0.08, anchorY: 0.90, z: 0},
    {name: 'head', x: 9, y: 2, width: 82, aspect: 339 / 400, anchorX: 0.50, anchorY: 0.85, z: 1},
    {name: 'page', x: 6, y: 54, width: 88, aspect: 309 / 376, anchorX: 0.50, anchorY: 0.50, z: 2},
    {name: 'left-paw', x: 1, y: 46, width: 26, aspect: 159 / 201, anchorX: 0.65, anchorY: 0.78, z: 3},
    {name: 'right-paw', x: 73, y: 46, width: 26, aspect: 159 / 200, anchorX: 0.35, anchorY: 0.78, z: 3},
  ];
  const asset = (name: string, scale: number) => `/ReportCat/${name}@${scale}x.png`;
  const sources = (name: string) => [1, 2, 3, 4].map(scale => `${asset(name, scale)} ${scale}x`).join(', ');
  const outward = 'cubic-bezier(0.33, 0.55, 0.66, 1)';
  const inward = 'cubic-bezier(0.33, 0, 0.66, 0.45)';
  // Core Animation's tail uses bottom-left coordinates; CSS rotation has the opposite sign.
  const tailFrames: Keyframe[] = [0, -10, 0, 10, 0].map((degrees, index) => ({
    offset: index / 4,
    transform: `rotate(${degrees}deg)`,
    easing: index % 2 === 0 ? outward : inward,
  }));
  function frames(times: number[], y: number[], degrees: number[], easing = 'cubic-bezier(0.42, 0, 0.58, 1)'): Keyframe[] {
    // Greeting values in the Swift source already describe CSS's top-left coordinates.
    return times.map((offset, index) => ({
      offset,
      transform: `translateY(${y[index] * unit}px) rotate(${degrees[index]}deg)`,
      easing,
    }));
  }
  const greetings = [
    {name: 'head', keyframes: frames([0, 0.18, 0.32, 0.50, 0.66, 0.79, 1],
      [0, -10, -10, -10, -10, -7, 0], [0, 0, -7, -7, 3, 0, 0], 'cubic-bezier(0.4, 0, 0.2, 1)')},
    {name: 'left-paw', keyframes: frames([0, 0.18, 0.66, 0.82, 1],
      [0, 0, 0, 0, 0], [0, -3, -3, 0, 0])},
    {name: 'right-paw', keyframes: frames([0, 0.22, 0.34, 0.46, 0.58, 0.68, 0.82, 1],
      [0, 0, -10, -10, -10, -4, 0, 0], [0, 0, -24, 10, -16, -6, 0, 0])},
  ];

  let button: HTMLButtonElement;
  let layers: Record<string, HTMLImageElement> = {};
  let preference: MediaQueryList;
  let mounted = false;
  let inView = false;
  let running = false;
  let tail: Animation | undefined;
  let greetingAnimations: Animation[] = [];
  let greetingTimer: ReturnType<typeof setTimeout> | undefined;

  function canAnimate(isSuspended: boolean) {
    return mounted && !isSuspended && inView && document.visibilityState === 'visible'
      && document.hasFocus() && !preference.matches;
  }
  function stopMotion() {
    if (greetingTimer !== undefined) clearTimeout(greetingTimer);
    greetingTimer = undefined;
    for (const animation of greetingAnimations) {
      animation.onfinish = null;
      animation.cancel();
    }
    greetingAnimations = [];
    tail?.cancel();
    tail = undefined;
    running = false;
  }
  function scheduleGreeting() {
    // One local wake-up every ten seconds; all frame interpolation stays in the compositor.
    greetingTimer = setTimeout(() => {
      greetingTimer = undefined;
      if (!canAnimate(suspended)) { stopMotion(); return; }
      greetingAnimations = greetings.map(({name, keyframes}) => {
        const animation = layers[name].animate(keyframes, {duration: 2200});
        animation.onfinish = () => {
          animation.onfinish = null;
          animation.cancel();
          greetingAnimations = greetingAnimations.filter(value => value !== animation);
        };
        return animation;
      });
      // Greeting completion never touches or restarts the tail's independent timeline.
      scheduleGreeting();
    }, 10000);
  }
  function synchronize(isSuspended: boolean) {
    const enabled = canAnimate(isSuspended);
    if (enabled === running) return;
    if (!enabled) { stopMotion(); return; }
    running = true;
    tail = layers.tail.animate(tailFrames, {duration: 3200, iterations: Infinity});
    scheduleGreeting();
  }
  $: if (mounted) synchronize(suspended);

  onMount(() => {
    preference = matchMedia('(prefers-reduced-motion: reduce)');
    const refresh = () => synchronize(suspended);
    const suspend = () => stopMotion();
    const observer = new IntersectionObserver(entries => {
      inView = entries.some(entry => entry.isIntersecting);
      refresh();
    });
    observer.observe(button);
    document.addEventListener('visibilitychange', refresh);
    window.addEventListener('focus', refresh);
    window.addEventListener('blur', suspend);
    window.addEventListener('pagehide', suspend);
    window.addEventListener('pageshow', refresh);
    preference.addEventListener('change', refresh);
    mounted = true;
    return () => {
      mounted = false;
      stopMotion();
      observer.disconnect();
      document.removeEventListener('visibilitychange', refresh);
      window.removeEventListener('focus', refresh);
      window.removeEventListener('blur', suspend);
      window.removeEventListener('pagehide', suspend);
      window.removeEventListener('pageshow', refresh);
      preference.removeEventListener('change', refresh);
    };
  });
</script>

<button bind:this={button} class="report-cat-button" type="button" onclick={onopen} aria-label={label} title={label}>
  <span class="report-cat-artwork" class:dark aria-hidden="true">
    {#each placements as layer (layer.name)}
      <img bind:this={layers[layer.name]} class="report-cat-layer" src={asset(layer.name, 1)} srcset={sources(layer.name)}
        alt="" draggable="false" decoding="async"
        style:left={`${layer.x * unit}px`} style:top={`${layer.y * unit}px`}
        style:width={`${layer.width * unit}px`} style:height={`${layer.width * unit * layer.aspect}px`}
        style:transform-origin={`${layer.anchorX * 100}% ${layer.anchorY * 100}%`} style:z-index={layer.z} />
    {/each}
  </span>
</button>

<style>
  .report-cat-button {
    position: relative;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex: 0 0 32px;
    width: 32px;
    min-width: 32px;
    max-width: 32px;
    height: 32px;
    min-height: 32px;
    max-height: 32px;
    padding: 0;
    border: 0;
    border-radius: 7px;
    background: transparent;
    cursor: pointer;
  }
  .report-cat-button:hover { background: var(--hover); }
  .report-cat-button:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
  .report-cat-artwork {
    position: relative;
    display: block;
    width: 21.96px;
    height: 23.04px;
    flex: none;
    pointer-events: none;
    user-select: none;
  }
  .report-cat-artwork.dark { filter: invert(1); }
  .report-cat-layer { position: absolute; display: block; max-width: none; pointer-events: none; }
</style>
