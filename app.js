const $ = (s, r = document) => [...r.querySelectorAll(s)];
addEventListener("load", () => setTimeout(() => $("#loader")[0]?.classList.add("hide"), 350));
$("#burger")[0]?.addEventListener("click", () => $("#menu")[0].classList.toggle("open"));
// scroll reveal + counting numbers
const io = new IntersectionObserver(es => es.forEach(e => {
  if (!e.isIntersecting) return; e.target.classList.add("in"); io.unobserve(e.target);
  if (e.target.querySelector(".count")) count(e.target.querySelector(".count"));
}), { threshold: .15 });
$(".reveal").forEach(el => io.observe(el));
function count(el) {
  const to = +el.dataset.to, t0 = performance.now();
  (function tick(t) { const p = Math.min((t - t0) / 1600, 1); el.textContent = Math.round(to * (1 - Math.pow(1 - p, 3))).toLocaleString(); if (p < 1) requestAnimationFrame(tick); })(t0);
}
// subtle 3D tilt that follows the mouse and eases back
$("[data-tilt]").forEach(c => {
  c.addEventListener("mousemove", e => { const r = c.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - .5, y = (e.clientY - r.top) / r.height - .5;
    c.style.transform = `perspective(800px) translateY(-8px) scale(1.03) rotateX(${-y * 8}deg) rotateY(${x * 8}deg)`; c.style.transition = "transform .1s"; });
  c.addEventListener("mouseleave", () => { c.style.transform = ""; c.style.transition = "transform .5s"; });
});
// button ripple + loading state on submit
$(".btn").forEach(b => b.addEventListener("click", e => { const r = b.getBoundingClientRect(), s = document.createElement("span"), d = Math.max(r.width, r.height);
  s.className = "ripple"; s.style.cssText = `width:${d}px;height:${d}px;left:${e.clientX - r.left - d / 2}px;top:${e.clientY - r.top - d / 2}px`; b.appendChild(s); setTimeout(() => s.remove(), 600); }));
$("form").forEach(f => f.addEventListener("submit", () => { const b = f.querySelector("button.btn:not(.sm)"); if (b && f.checkValidity()) setTimeout(() => b.classList.add("loading"), 0); }));
$(".flash").forEach(f => setTimeout(() => { f.style.transition = "opacity .6s"; f.style.opacity = 0; }, 5000));
