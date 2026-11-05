"""python -m mlng_ai <명령>   (ai/ 폴더에서)

  fetch            공개 데이터(UCI) 받기 → ai/data/public
  train            전체 학습 · 검증 → ai/state/model.json · web/data/ai_model.json · ai/reports/report.md
  train --fast     검증 일부(처음 보는 공정 · 가상 주입) 생략 — 빠른 확인용
  map 파일.csv     처음 보는 CSV 의 칼럼을 해석해 표로 보여 준다 (--llm: 애매한 칼럼만 Claude 에 물어봄)
  compare4         4방식 비교 실험(규칙 · 자체 학습 · 공통 그대로 · 공통+추가 학습) → reports/compare4.md
  mct 요약폴더 [--site 현장요약폴더] [--vb 120]   (--vb: Stiehl 데이터 교체 임박 기준 µm)
                   MCT 기본 모델 — HMoTP · NASA 로 학습 · 공구 단위 평가 · 정상 기준 모드 검증
                   → reports/mct.md · state/base/mct.json (현장 결과는 현장 폴더에만 저장)
  stiehl h5폴더 요약폴더 [--workers 4]
                   Mendeley 밀링 마모 데이터(설비 3대 · 공구 9개, h5) → 요약폴더/stiehl_runs.csv (이어서 실행 가능)
  diagnose 파일.csv|xlsx [--unit-cost 15000] [--out 리포트.html]   (UTF-8 · CP949 자동)
                   고객 데이터 진단 — 칼럼 해석 · 데이터 품질 · 불량 원인 후보 · 손실 금액 리포트 (HTML)
"""
import sys
import warnings

warnings.filterwarnings('ignore')


def main(argv):
    cmd = argv[1] if len(argv) > 1 else 'help'
    if cmd == 'fetch':
        from .datasets import fetch_public
        print('받음:', fetch_public() or '이미 있음')
    elif cmd == 'train':
        from .export import REPORT, run
        a = run(fast='--fast' in argv)
        print(f"완료 — 누적 갱신 {a['model_version']}회 · 칼럼 해석(처음 보는 이름) {a['schema_eval']['holdout']:.1%} · 보고서 {REPORT}")
        for k, r in a['prequential'].items():
            print(f"  {k:6s} 이탈도 개선 {r['gated_skill']:+.1%}  게이트 {r['gate']:.2f}  " + (f"AUC {r['auc']:.2f}" if r.get('auc') is not None else f"순위상관 {r.get('spearman') or 0:.2f}"))
    elif cmd == 'map' and len(argv) > 2:
        import pandas as pd
        from .schema import sense_table
        df = pd.read_csv(argv[2])
        for c, s in sense_table(df, llm='--llm' in argv).items():
            print(f"{c[:28]:28s} {s.role:14s} {s.quantity:12s} {s.confidence:.2f}  {'; '.join(s.why)[:70]}")
    elif cmd == 'compare4':
        import json
        from pathlib import Path
        from .engine import build
        from .evaluate import compare4, compare4_md
        E = build()
        res = [compare4(E, k) for k in E]
        out = Path(__file__).resolve().parent.parent / 'reports'
        out.mkdir(exist_ok=True)
        (out / 'compare4.md').write_text(compare4_md(res), encoding='utf-8')
        (out / 'compare4.json').write_text(json.dumps(res, ensure_ascii=False, default=float, indent=1), encoding='utf-8')
        print(compare4_md(res))
    elif cmd == 'stiehl' and len(argv) > 3:
        from .stiehl import main as st_main
        st_main(argv[2:])
    elif cmd == 'mct' and len(argv) > 2:
        from pathlib import Path
        from .mct import run
        root = Path(__file__).resolve().parent.parent
        site = argv[argv.index('--site') + 1] if '--site' in argv else None
        kw = {'stiehl_um': float(argv[argv.index('--vb') + 1])} if '--vb' in argv else {}
        r = run(argv[2], root / 'reports', root / 'state' / 'base', site, **kw)
        print((root / 'reports' / 'mct.md').read_text(encoding='utf-8'))
        if r.get('site'):
            print(f"현장 결과 → {site}/report_site.md (저장소에 넣지 않음)")
    elif cmd == 'diagnose' and len(argv) > 2:
        from pathlib import Path
        import pandas as pd
        from .diagnose import diagnose, read_table, render_html
        from .model import CumulativeModel
        opt = lambda k: argv[argv.index(k) + 1] if k in argv and argv.index(k) + 1 < len(argv) else None   # noqa: E731
        src = Path(argv[2])
        df = pd.read_csv(src, encoding=opt('--encoding')) if opt('--encoding') else read_table(str(src))
        seed = Path(__file__).resolve().parent.parent / 'state' / 'model.json'
        M = CumulativeModel.load(seed) if seed.exists() else None
        uc = opt('--unit-cost')
        r = diagnose(df, unit_cost=float(uc) if uc else None, model=M, name=src.name)
        out = Path(opt('--out') or src.with_suffix('.진단.html'))
        out.write_text(render_html(r), encoding='utf-8')
        print(f"결과 칼럼 {r['outcome'] or '없음'} · 준비도 {r['readiness']['score']}/100 · 원인 후보 {len(r['factors'])}개 → {out}")
        for f in r['factors'][:5]:
            print(f"  {f['label'][:28]:28s} {f['worst'][:24]:24s} 불량률 {f['worst_rate']:.1%} (나머지 {f['rest_rate']:.1%}, {f['lift']:.1f}배)")
    else:
        print(__doc__)


if __name__ == '__main__':
    main(sys.argv)
