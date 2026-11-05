const menu = document.querySelector('.menubutton');
const nav = document.getElementById('navigation');
menu.addEventListener('click', () => {
  const open = menu.getAttribute('aria-expanded') !== 'true';
  menu.setAttribute('aria-expanded', String(open));
  menu.setAttribute('aria-label', open ? '메뉴 닫기' : '메뉴 열기');
  nav.classList.toggle('open', open);
});
nav.querySelectorAll('a').forEach(a => a.addEventListener('click', () => {
  menu.setAttribute('aria-expanded', 'false'); menu.setAttribute('aria-label', '메뉴 열기'); nav.classList.remove('open');
}));
const dialog = document.getElementById('demoDialog');
const frame = document.getElementById('demoFrame');
function openDemo() {
  if (!frame.getAttribute('src')) frame.src = frame.dataset.src;
  dialog.showModal(); document.body.classList.add('dialog-open');
}
document.getElementById('demoPreview').addEventListener('click', openDemo);
document.getElementById('demoButton').addEventListener('click', openDemo);
document.getElementById('closeDemo').addEventListener('click', () => dialog.close());
dialog.addEventListener('close', () => document.body.classList.remove('dialog-open'));
const form = document.getElementById('contactForm');
function brief() {
  const d = new FormData(form);
  return {subject: `[MILG 문의] ${d.get('type')} — ${d.get('company')}`,
    body: `회사명: ${d.get('company')}\n담당자: ${d.get('person')}\n회신 이메일: ${d.get('email')}\n문의 유형: ${d.get('type')}\n\n검토할 공정과 현재 상황\n${d.get('message')}\n`};
}
form.addEventListener('submit', e => {
  e.preventDefault(); const b = brief();
  window.location.href = `mailto:MILGAICEO@gmail.com?subject=${encodeURIComponent(b.subject)}&body=${encodeURIComponent(b.body)}`;
  document.getElementById('formStatus').textContent = '메일 앱에서 내용을 확인하고 전송해 주세요. 앱이 열리지 않으면 검토서를 다운로드해 위 이메일로 보내실 수 있습니다.';
});
document.getElementById('downloadBrief').addEventListener('click', () => {
  if (!form.reportValidity()) return;
  const b = brief(); const url = URL.createObjectURL(new Blob(['\uFEFF' + b.subject + '\n\n' + b.body], {type:'text/plain;charset=utf-8'}));
  const a = document.createElement('a'); a.href = url; a.download = 'MILG-도입검토서.txt';
  document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  document.getElementById('formStatus').textContent = '검토서를 다운로드했습니다. MILGAICEO@gmail.com으로 보내주세요.';
});
document.getElementById('year').textContent = new Date().getFullYear();
