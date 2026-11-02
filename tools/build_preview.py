"""web/ 폴더를 한 파일(preview.html)로 묶는다 — 더블클릭으로 바로 열리는 데모.
서버도, Supabase 도, 인터넷도 필요 없다. 고객 시연·발표용.

  python build_preview.py
"""
import base64, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(os.path.dirname(HERE), 'web')
OUT = os.path.join(os.path.dirname(HERE), 'preview.html')


def read(p):
    return open(os.path.join(WEB, p), encoding='utf-8').read()


def main():
    html = read('index.html')
    css = read('css/style.css')
    html = re.sub(r'<link rel="stylesheet" href="css/style\.css(\?v=[0-9a-f]+)?">', lambda m: f'<style>\n{css}\n</style>', html)
    demo = json.load(open(os.path.join(WEB, 'data', 'demo.json'), encoding='utf-8'))
    aip = os.path.join(WEB, 'data', 'ai_model.json')     # 공정 통합 AI 결과물 (있으면 함께 넣는다 — file:// 에서는 fetch 가 안 되므로)
    ai = f'<script>window.MLNG_AI = {open(aip, encoding="utf-8").read()};</script>\n' if os.path.exists(aip) else ''
    cfg = read('js/config.js')
    cfg = re.sub(r"SUPABASE_URL: '[^']*'", "SUPABASE_URL: ''", cfg)       # 미리보기는 언제나 데모 모드
    cfg = re.sub(r"SUPABASE_ANON_KEY: '[^']*'", "SUPABASE_ANON_KEY: ''", cfg)
    # 스크립트 목록은 index.html 이 정한다 (한 곳에서만 관리) — config.js 는 데모용으로 고쳐서 따로 넣는다
    idx_js = re.findall(r'<script src="js/([^"?]+)(?:\?v=[0-9a-f]+)?"></script>', html)
    js = [j for j in idx_js if j not in ('config.js',)]
    pre = [j for j in js if js.index(j) < idx_js.index('config.js')] if 'config.js' in idx_js else []
    js = [j for j in js if j not in pre]
    scripts = (''.join(f'<script>\n{read("js/" + j)}\n</script>\n' for j in pre) + f'<script>\n{cfg}\n</script>\n<script>window.MLNG_DEMO = {json.dumps(demo, ensure_ascii=False, separators=(",", ":"))};</script>\n' + ai
               + '\n'.join(f'<script>\n{read("js/" + j)}\n</script>' for j in js))
    html = re.sub(r'<script src="js/[^"]+"></script>.*<script src="js/boot\.js(\?v=[0-9a-f]+)?"></script>', lambda m: scripts, html, flags=re.S)
    open(OUT, 'w', encoding='utf-8').write(html)
    print(f'[완료] {OUT} ({os.path.getsize(OUT) / 1024:.0f} KB)')


if __name__ == '__main__':
    main()
