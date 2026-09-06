import * as THREE from 'three';
import { createIcons, Pause, Play } from 'lucide';

export function mountLakeScene(hero, container) {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let renderer;
  try { renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'low-power' }); }
  catch { hero.querySelector('#motion-toggle')?.setAttribute('hidden', ''); return () => {}; }
  renderer.setPixelRatio(Math.min(devicePixelRatio, innerWidth < 650 ? 1.25 : 1.75));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.domElement.setAttribute('aria-label', 'Анимированный вид бухты Максимиха');
  renderer.domElement.dataset.scene = 'baikal';
  container.append(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, .1, 20); camera.position.z = 3;
  const texture = new THREE.TextureLoader().load('/media/maximikha.jpg', () => { if (disposed) return; uniforms.photoAspect.value = texture.image.width / texture.image.height; render(); renderer.domElement.classList.add('ready'); }, undefined, () => { running = false; button.hidden = true; });
  texture.colorSpace = THREE.SRGBColorSpace;
  const uniforms = { photo: { value: texture }, time: { value: 0 }, aspect: { value: 1 }, photoAspect: { value: 3840 / 1864 }, pointer: { value: new THREE.Vector2() }, clickTime: { value: -100 }, ripple: { value: new THREE.Vector2(.5,.2) } };
  const material = new THREE.ShaderMaterial({ uniforms,
    vertexShader: `varying vec2 vUv; uniform float time; uniform vec2 pointer;
      void main(){vUv=uv;vec3 p=position;float water=1.0-smoothstep(.36,.52,uv.y);
      p.z+=water*(sin(uv.x*18.0+time*.55)*cos(uv.y*20.0-time*.45)*.025);
      p.z+=pow(abs(uv.x-.5)*2.0,2.0)*.035;
      gl_Position=projectionMatrix*modelViewMatrix*vec4(p,1.0);}`,
    fragmentShader: `varying vec2 vUv;uniform sampler2D photo;uniform float time;uniform float aspect;uniform float photoAspect;uniform vec2 pointer;uniform float clickTime;uniform vec2 ripple;
      void main(){vec2 uv=vUv; if(aspect>photoAspect){uv.y=(uv.y-.5)*(photoAspect/aspect)+.5;}else{uv.x=(uv.x-.5)*(aspect/photoAspect)+.5;}
      float water=1.0-smoothstep(.35,.46,uv.y);float depth=1.0-uv.y;
      uv.x+=water*sin(uv.y*140.0+time*1.1)*.0012*depth;
      uv.y+=water*(sin(uv.x*25.0+time*.6)+sin(uv.y*90.0-time*.8))*.0007;
      float elapsed=time-clickTime;float dist=distance(vUv,ripple);float ring=sin(dist*80.0-elapsed*5.0)*exp(-dist*3.0)*exp(-elapsed*1.2)*step(0.0,elapsed);
      uv+=water*ring*.0018;vec3 color=texture2D(photo,clamp(uv,.002,.998)).rgb;
      color*=1.28; color=mix(color,vec3(dot(color,vec3(.2126,.7152,.0722))),.08);
      gl_FragColor=vec4(color,1.0);
      #include <tonemapping_fragment>
      #include <colorspace_fragment>
      }`,
  });
  const geometry = new THREE.PlaneGeometry(1, 1, 80, 50);
  const mesh = new THREE.Mesh(geometry, material); scene.add(mesh);
  let width = 1, height = 1, frame, last = 0, running = !reduced, visible = true, disposed = false;
  const target = new THREE.Vector2();
  function resize() {
    width = container.clientWidth; height = container.clientHeight;
    if (!width || !height) return;
    renderer.setSize(width, height); camera.aspect = width / height; camera.updateProjectionMatrix(); uniforms.aspect.value = width / height;
    const h = 2 * Math.tan(THREE.MathUtils.degToRad(20)) * 3;
    mesh.scale.set(h * camera.aspect * 1.06, h * 1.06, 1); render();
  }
  function render() { if (!disposed) renderer.render(scene, camera); }
  function tick(now) {
    if (disposed || !running || !visible || document.hidden) { frame = null; last = 0; return; }
    if (last) uniforms.time.value += Math.min((now-last)/1000,.05);
    last = now; uniforms.pointer.value.lerp(target,.035); camera.position.x = uniforms.pointer.value.x * .055; camera.position.y = uniforms.pointer.value.y * .035; camera.lookAt(0,0,0);
    render(); frame = requestAnimationFrame(tick);
  }
  function start() { if (!frame && running && visible && !document.hidden) frame = requestAnimationFrame(tick); }
  const abort = new AbortController();
  hero.addEventListener('pointermove', e => { if (reduced) return; const bounds = hero.getBoundingClientRect(); target.set(((e.clientX-bounds.left)/width-.5)*2, -((e.clientY-bounds.top)/height-.5)*2); }, { signal: abort.signal });
  hero.addEventListener('pointerleave', () => target.set(0,0), { signal: abort.signal });
  hero.addEventListener('pointerdown', e => { if (reduced || e.target.closest('a,button')) return; const bounds = hero.getBoundingClientRect(); uniforms.ripple.value.set((e.clientX-bounds.left)/width, 1-(e.clientY-bounds.top)/height); uniforms.clickTime.value = uniforms.time.value; }, { signal: abort.signal });
  document.addEventListener('visibilitychange', start, { signal: abort.signal });
  const button = hero.querySelector('#motion-toggle');
  function label() { button.title = button.ariaLabel = running ? 'Приостановить анимацию' : 'Включить анимацию'; button.setAttribute('aria-pressed', String(!running)); button.innerHTML = `<i data-lucide="${running ? 'pause' : 'play'}" aria-hidden="true"></i>`; createIcons({ icons: { Pause, Play }, root: button }); }
  label(); button.addEventListener('click', () => { running = !running; label(); start(); }, { signal: abort.signal });
  const observer = new IntersectionObserver(entries => { visible = entries[0].isIntersecting; start(); }); observer.observe(hero);
  const resizeObserver = new ResizeObserver(resize); resizeObserver.observe(container); resize(); start();
  return () => { disposed = true; cancelAnimationFrame(frame); abort.abort(); observer.disconnect(); resizeObserver.disconnect(); geometry.dispose(); material.dispose(); texture.dispose(); renderer.dispose(); renderer.domElement.remove(); };
}
