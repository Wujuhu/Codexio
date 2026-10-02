import { mount } from 'svelte'
import App from './App.svelte'

if (new URLSearchParams(location.search).get('view') === 'floating') document.documentElement.classList.add('floating-document')
mount(App, { target: document.getElementById('app')! })
