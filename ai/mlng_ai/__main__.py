"""python -m mlng_ai <명령>   (ai/ 폴더에서)

  fetch            공개 데이터(UCI) 받기 → ai/data/public
  train            전체 학습 · 검증 → ai/state/model.json · web/data/ai_model.json · ai/reports/report.md
  train --fast     검증 일부(처음 보는 공정 · 가상 주입) 생략 — 빠른 확인용
  map 파일.csv     처음 보는 CSV 의 칼럼을 해석해 표로 보여 준다 (--llm: 애매한 칼럼만 Claude 에 물어봄)
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
    else:
        print(__doc__)


if __name__ == '__main__':
    main(sys.argv)
