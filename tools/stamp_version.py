"""코드 빌드 스탬프 — 파일마다 해시를 찍어 (1) 브라우저 캐시 문제를 없애고 (2) «어떤 코드가 판단했나» 를 증명한다.

  python tools/stamp_version.py          index.html 의 js·css 에 ?v=<해시 8자리> 를 붙이고 web/js/build.js 를 다시 쓴다
  python tools/stamp_version.py --check  고칠 것이 있으면 실패 (CI 에서 «스탬프를 안 찍고 push» 를 막는다)

build.js : window.MLNG_BUILD = { hash: 전체 해시, files: { 파일: 해시 } } — 판단 증명 화면과 원장 기록의 «코드» 값.
시각은 넣지 않는다 (같은 코드 = 같은 해시 = 같은 build.js).
"""
import hashlib, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, 'web')


def h(path):
    # 줄바꿈을 LF 로 맞춰 해시 — Windows(CRLF) 에서 찍어도 CI(Linux · LF) 와 같은 값
    return hashlib.sha256(open(path, 'rb').read().replace(b'\r\n', b'\n')).hexdigest()


def main():
    check = '--check' in sys.argv
    idx_p = os.path.join(WEB, 'index.html')
    idx = open(idx_p, encoding='utf-8').read()
    srcs = re.findall(r'<script src="(js/[^"?]+)(?:\?v=[0-9a-f]+)?"></script>', idx)
    files = {s: h(os.path.join(WEB, s))[:16] for s in srcs if s != 'js/build.js'}
    files['css/style.css'] = h(os.path.join(WEB, 'css', 'style.css'))[:16]
    total = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    build = ('/* 자동 생성 — tools/stamp_version.py. 손으로 고치지 않는다 */\n'
             f'window.MLNG_BUILD = {json.dumps({"hash": total, "files": files}, ensure_ascii=False, indent=1)};\n')
    new = re.sub(r'<script src="(js/[^"?]+)(?:\?v=[0-9a-f]+)?"></script>', lambda m: f'<script src="{m.group(1)}?v={(files.get(m.group(1)) or total)[:8]}"></script>', idx)
    new = re.sub(r'<link rel="stylesheet" href="css/style\.css(?:\?v=[0-9a-f]+)?">', f'<link rel="stylesheet" href="css/style.css?v={files["css/style.css"][:8]}">', new)
    if '<script src="js/build.js' not in new:
        new = new.replace('<script src="js/config.js', '<script src="js/build.js?v=0"></script>\n<script src="js/config.js', 1)
        new = re.sub(r'<script src="js/build\.js\?v=[0-9a-f]+"></script>', f'<script src="js/build.js?v={total[:8]}"></script>', new)
    bp = os.path.join(WEB, 'js', 'build.js')
    old_build = open(bp, encoding='utf-8').read() if os.path.exists(bp) else ''
    changed = new != idx or old_build != build
    if check:
        if changed:
            print('[실패] 스탬프가 코드와 다릅니다 — python tools/stamp_version.py 를 실행하고 다시 커밋하세요'); sys.exit(1)
        print(f'[확인] 스탬프 일치 · 빌드 {total[:12]}'); return
    open(idx_p, 'w', encoding='utf-8').write(new)
    open(bp, 'w', encoding='utf-8').write(build)
    print(f'[완료] 빌드 {total[:12]} · 파일 {len(files)}개 스탬프')


if __name__ == '__main__':
    main()
