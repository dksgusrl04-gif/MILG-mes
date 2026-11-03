/* MILG MES 설정
 * ───────────────────────────────────────────────────────────────
 * 비워 두면 «데모 모드» — data/demo.json 만 보여 준다.
 * 채우면 Supabase 에서 읽는다 (로그인 없음, anon 키). 표가 비어 있으면 데모 스냅샷을 계속 보여 준다.
 * MILG 전용 Supabase 프로젝트 값 — MCT MES 프로젝트와 섞지 않는다.
 * anon 키는 공개돼도 되는 키다. 절대 넣으면 안 되는 것: service_role 키
 */
window.MLNG_CONFIG = {
  SUPABASE_URL: 'https://vwgfeutvcvogutxuhcxt.supabase.co',   // MILG MES 전용 프로젝트
  SUPABASE_ANON_KEY: 'sb_publishable_7FxcyivB8vrrJJdrWTSl9g_FZUiEV17',   // Publishable key (공개용)
  // 공정 통합 AI 엔진 주소 (데이터 진단 업로드) — 비우면 SUPABASE_URL 을 쓴다 (자체 서버에서는 같은 주소)
  AI_URL: '',
  // 첫 화면 — 'tx_report' = 불량 원인 분석 보고서 (MILG Trace) · 'overview' = 종합 현황
  START_VIEW: 'tx_report',

  // 아직 이 셸에 합치지 않은 모듈 — 공정 흐름에서 누르면 새 탭으로 연다 (비우면 링크 없음)
  LINKS: {
    weld: '',                                              // 용접 웹 주소 (배포 후)
  },

  // 운영 기준 — 현장에서 정해 바꾼다
  OUTPUT_PROC: 'heat',        // 공장 «생산 수량» 을 세는 공정 (검사 셀은 가공 사양 부품만 거치므로 열처리 완료 기준)
  RECENT_DAYS: 7,             // «최근» 으로 보는 기간 (알림·KPI)
  LIVE_SEC: 30,               // 실시간 반영: 서버에서 다시 읽는 간격 (초) — 상단 「실시간 반영」 단추로 켜고 끈다
  STALE_HOURS: 24,            // 실시간 모드에서 이 시간 넘게 새 작업이 없으면 알림
  HEAT: {
    TARGET_PPM: null,         // 불량률 관리 기준. 비우면 데이터에 담긴 값(450 PPM)
    DEVIATION_RATE: 0.05,     // 한 작업에서 조건을 벗어난 시간이 이 비율을 넘으면 «조건 이탈»
    HEATER_DRIFT_PP: 5,       // 히터 출력이 초기 대비 이만큼(%p) 늘면 설비 점검 알림
    HEATER_BASE_JOBS: 20,     // 히터 출력 기준 = 처음 N개 작업의 중앙값, 비교 = 최근 N개
  },
  PRESS: {
    TARGET_PPM: 150,          // 프레스 불량률 관리 기준 (부품 기준 PPM)
    TREND_DAYS: 3,            // 최근 N작업일 불량률이
    TREND_RATIO: 2,           //   그 전보다 이 배수 이상이면 «불량 증가» 알림
    OUT_RATE: 0.01,           // 하루 타발 중 허용 범위 밖이 이 비율 이상이면 알림
    CPK_OK: 1.33, CPK_MIN: 1.0,
  },
  PLAN: {
    AVG_DAYS: 7,              // 내일 예상 수주 = 최근 N작업일 평균
    SAFETY_DAYS: 0.5,         // 안전재고 = 예상 수주의 이 비율
    LOT_SIZE: { 'GR-021': 150, 'GR-042': 200 },   // 1회 생산량
  },
  MACH: {
    VB_LIMIT: 0.5,            // 공구 교체 기준 — 플랭크 마모 VB (mm)
    VB_WARN: 0.35,            // 주의 기준
    LATE_RATIO: 1.2,          // 최종 VB 가 교체 기준의 이 배수를 넘으면 «늦은 교체»
  },
  // 공구 주문 — 등록 품목은 js/catalog.js. 추천이 없어도 골라서 발주할 수 있다
  BUY: {
    COMPANY: 'MILG 데모 공장',          // 발주서 머리
    LINE: '시트 트랙 스토퍼 브래킷 라인',
    DEPT: '생산기술팀',                 // 요청부서
    STEPS: ['담당', '검토', '승인'],     // 결재 단계 (순서대로)
    MACH_STOCK: 12,                     // 페이스커터 인서트 현재 재고 (데모값)
    MACH_WEEKS: 2,                      // 인서트는 «최근 7일 공구 교체 횟수 × 날 수 × N주» 만큼 확보
  },
  // 2.0 탄소 발자국 — 모두 예시값. 최신 국가 전력 배출계수·공급사 제공값으로 바꿔 쓴다
  PCF: {
    EF_KWH: 0.4594,            // 전력 배출계수 kgCO2e/kWh (예시)
    STEEL_KG: 0.35, STEEL_EF: 2.3,   // 브래킷 1개 강판 kg · 강판 kgCO2e/kg (예시, 공급사 값으로 교체)
    HEAT_ZONE_KW: { op1: 60, op2: 60, op3: 60, op4: 60, opd1: 20, opd2: 20 },   // 열처리 존별 정격 kW
    PRESS_KWH_PER_STROKE: 0.0022, WELD_KWH_PER_SPOT: 0.0015, SPOTS_PER_PART: 2, MACH_KW: 7.5, INSP_KWH_PER_PART: 0.002,
  },
  // 3.0 돈으로 보는 공장 — 모두 예시값. 회사 원가표·전기요금·배출권 가격으로 바꿔 쓴다
  COST: {
    PRICE: 420,               // 브래킷 판매 단가 (원/개)
    MARGIN: 120,              // 공헌이익 (원/개) = 단가 − 재료비 − 변동 가공비 → 못 만든 1개의 기회 손실
    SCRAP: { press: 160, weld: 185, heat: 230, mach: 1800, insp: 1800 },   // 그 공정에서 불량 1개 = 그때까지 들어간 재료·가공비 (원)
    MACH_PART_PRICE: 2600,    // 가공 사양 부품 단가 (원/개) — 검사 보류 묶임 금액
    INSERT: 12000,            // 페이스커터 인서트 1개 (원)
    KWH: 140,                 // 산업용 전기 요금 (원/kWh)
    INSP_PART: 350,           // 부품 1개 전수 측정 비용 (원)
    OVERWEAR_DEFECT: 0.08,    // 교체 기준을 넘긴 공구로 가공한 부품의 불량 비율 (가정)
    CLAIM_FIXED: 500000,      // 클레임 1건 대응 고정비 (원)
    CLAIM_PER_PART: 12,       // 클레임 LOT 선별·재검사 비용 (원/개)
    STOP_H: 4,                // 설비 고장 시 정지 시간 (h)
    STOP_P: 0.3,              // 진동 이상이 방치되면 고장으로 이어질 확률 (가정)
    MAINT: 250000,            // 설비 점검 1회 (원)
    PLAN_PRICE: 3000,         // 계획 품목(기어) 1개 결품 손실 (원)
    CO2_WON: 10,              // 탄소 비용 (원/kgCO2e, 배출권 가격 예시)
    HOLD_FIXED: 150000,       // 출하 보류 1건 비용 — 재검사 · 일정 조정 (원)
    HOLD_PER_PART: 4,         // 출하 보류 개당 비용 — 보관 · 선별 (원/개)
  },
  // 알람 등급 — A 즉시 조치(라인 정지 · 고객 출하 영향) · B 당일 조치(품질 · 설비 이상) · C 관찰(추세 · 데이터 점검)
  //   ISA-18.2 권장 분포(상 5% · 중 15% · 하 80%)를 참고해 A 를 최소로 둔다. 여기 없는 알람은 원래 수준(긴급 A · 주의 B · 정보 C)
  ALARM_GRADE: {
    'press.vib': 'A', 'mach.tool_over': 'A', 'gate.hold': 'A',
    'deal.claim': 'B', 'heat.ppm_over': 'B', 'heat.dev': 'B', 'insp.conflict': 'B', 'weld.pred': 'B', 'plan.short': 'B', 'press.ppm_trend': 'B',
    'heat.heater': 'C', 'insp.stuck': 'C', 'mach.late': 'C', 'weld.pressure': 'C', 'press.ppm_over': 'C', 'mach.sensor': 'C',
  },
  // 신호등 기준 — 지표가 목표의 몇 % 이상이면 초록 · 노랑 (그 아래 빨강)
  SIGNAL: { OEE_GREEN: 0.85, OEE_YELLOW: 0.65, PLAN_GREEN: 0.95, PLAN_YELLOW: 0.85 },
  // OEE — 설비별 계획 가동 시간(분/가동일)과 이론 능력(가정값). 가정값 관리에서 바꾼다
  OEE: {
    PLANNED: { 'PR-01': 460, 'SP-01': 460, 'SB-01': 1320, 'MC-01': 460, 'IN-01': 1320 },   // 1교대 8h−휴식 20분 · 2교대 24h−휴식 2h
    IDEAL: {},                // 이론 생산 속도 (개/분) — 비우면 데이터의 상위 10% 가동일 속도 (설비 제원값을 알면 넣는다)
    TARGET: 0.85,
  },
  // 3.0 교대 — 주간 시작 시각 (12시간 2교대)
  SHIFT: { DAY_START: 8, HOURS: 12 },
  // 2.0 공급망 동기 계획 — 다른 회사 능력은 데이터 계약으로 받는 값 (데모는 예시값)
  SYNC: { SEATS_PER_VEHICLE: 2, BRACKETS_PER_SEAT: 2, B_SEAT_CAP: 26000, STEEL_KG: 0.35, STEEL_YIELD: 0.72, D_STEEL_TPD: 22 },
  // 질의응답 (Claude API) — 키는 Supabase Edge Function 에만 둔다 (supabase/functions/mlng-ask)
  //   '' = `${SUPABASE_URL}/functions/v1/mlng-ask` 를 쓴다 (배포 완료) · 'off' = 규칙 기반만
  ASK_URL: '',
  RETRAIN_MIN_RECORDS: 50,
};
// 자체 서버에서 실행 중이면 Nginx 가 내려 준 값으로 덮어쓴다 (js/runtime.js · server/nginx)
if (window.MLNG_RUNTIME) Object.assign(window.MLNG_CONFIG, window.MLNG_RUNTIME);
