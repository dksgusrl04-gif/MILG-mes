"""Render the evidence-aligned Korean business overview (not a training script)."""
from pathlib import Path
import json
import math
import shutil
from xml.sax.saxutils import escape

from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph
from reportlab.lib.utils import ImageReader

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output'/'pdf'
TMP=ROOT/'tmp'/'pdfs'/'business-overview-20261112'
OUT.mkdir(parents=True,exist_ok=True);TMP.mkdir(parents=True,exist_ok=True)
PDF=OUT/'MILG_Engine_사업설명서_20261112_개정본.pdf'
ARCHIVE=Path('C:/Users/dksgu/Downloads/MILG_Training_Packs_20261107/_최종_시연본_학습데이터아님')
FONT=Path('C:/Windows/Fonts')
pdfmetrics.registerFont(TTFont('KR',str(FONT/'malgun.ttf')))
pdfmetrics.registerFont(TTFont('KR-Bold',str(FONT/'malgunbd.ttf')))
pdfmetrics.registerFontFamily('KR',normal='KR',bold='KR-Bold',italic='KR',boldItalic='KR-Bold')
W,H=595.276,841.89
M=44; CW=W-M*2
INK='#18283F';MUTED='#67768B';BRAND='#5145E5';CYAN='#0CA8C4';LINE='#E1E7F0';PALE='#F4F6FC';WHITE='#FFFFFF'
c=canvas.Canvas(str(PDF),pagesize=(W,H))
c.setTitle('MILG Engine 사업설명서 | 현재 구현 및 판매 전략 | 2026.11.12')
c.setAuthor('MILG');c.setSubject('제조 AI 엔진·MES 위젯·판매 상품·파트너 공급·검증 현황')
PAGE=0

def color(s):return colors.HexColor(s)
def rect(x,top,w,h,fill=PALE,stroke=None,r=8):
 c.setFillColor(color(fill));c.setStrokeColor(color(stroke or fill));c.roundRect(x,H-top-h,w,h,r,stroke=bool(stroke),fill=1)
def txt(text,x,top,size=10.4,bold=False,fill=INK):
 c.setFont('KR-Bold' if bold else 'KR',size);c.setFillColor(color(fill));c.drawString(x,H-top-size,text)
def para(text,x,top,w=CW,size=10.3,leading=None,fill=INK,bold=False):
 style=ParagraphStyle('body',fontName='KR-Bold' if bold else 'KR',fontSize=size,leading=leading or size*1.56,textColor=color(fill),wordWrap='CJK',splitLongWords=True,spaceAfter=0)
 p=Paragraph(text,style);_,height=p.wrap(w,1000)
 if top+height>H-65:raise ValueError(f'Page {PAGE} overflow: {top+height:.1f}: {text[:50]}')
 p.drawOn(c,x,H-top-height);return top+height
def line(y,x=M,w=CW):
 c.setStrokeColor(color(LINE));c.setLineWidth(.6);c.line(x,H-y,x+w,H-y)
def label(text,y):txt(text,M,y,10.2,True,BRAND);return y+25
def bullets(items,y,x=M,w=CW,size=10.2,gap=9):
 for s in items:
  txt('•',x,y,size,True,BRAND);y=para(s,x+14,y,w-14,size)+gap
 return y
def callout(title,body,y,height=90,x=M,w=CW):
 rect(x,y,w,height);txt(title,x+16,y+13,11.4,True,BRAND)
 para(body,x+16,y+35,w-32,10.1)
 return y+height+18
def table(headers,rows,widths,y,size=9.3):
 widths=[CW*v/sum(widths) for v in widths]
 def row(values,y,header=False,idx=0):
  cells=[];heights=[]
  for value,w in zip(values,widths):
   p=Paragraph(str(value),ParagraphStyle('cell',fontName='KR-Bold' if header else 'KR',fontSize=size,leading=size*1.48,textColor=color(BRAND if header else INK),wordWrap='CJK'))
   _,h=p.wrap(w-18,1000);cells.append(p);heights.append(h)
  h=max(heights)+19
  if y+h>H-62:raise ValueError(f'Page {PAGE}: table overflow at {y+h}')
  c.setFillColor(color('#EEF0FC' if header else WHITE if idx%2 else '#F8FAFD'));c.rect(M,H-y-h,CW,h,stroke=0,fill=1)
  x=M
  for p,w,ph in zip(cells,widths,heights):p.drawOn(c,x+9,H-y-9-ph);x+=w
  line(y+h);return y+h
 y=row(headers,y,True)
 for i,values in enumerate(rows):y=row(values,y,idx=i)
 return y+18
def header(number,section,title,sub='',sources=''):
 global PAGE
 if PAGE:c.showPage()
 PAGE+=1
 c.bookmarkPage(f'section_{number:02d}');c.addOutlineEntry(f'{number:02d} {section}',f'section_{number:02d}',level=0)
 txt('MILG',M,24,15,True,BRAND);txt('ENGINE  /  BUSINESS OVERVIEW',M+66,29,7.2,False,MUTED)
 txt(f'{number:02d}  {section}',M,68,9.1,True,BRAND)
 y=para(title,M,91,CW,23.5,31,bold=True)
 if sub:y=para(sub,M,y+10,CW,10.1,15,fill=MUTED)
 line(H-35)
 txt('MILG Engine · 사업 설명서 · 2026.11.12',M,H-27,7.1,False,MUTED)
 c.setFont('KR',7.2);c.setFillColor(color(MUTED));c.drawRightString(W-M,H-(H-27)-7.2,f'{PAGE:02d} / 16')
 if sources:txt('근거  '+sources,M,H-51,7.1,False,MUTED)
 return y+24
def image(path,x,y,w,h=None):
 im=Image.open(path);height=h or w*im.height/im.width;c.drawImage(ImageReader(im),x,H-y-height,w,height,mask='auto');return y+height

# 01 — Cover
PAGE=1
txt('MILG',M,53,38,True,BRAND);txt('| AI',M+137,63,24,True,CYAN)
txt('MANUFACTURING INTELLIGENCE',M,113,9,True,MUTED)
para('MILG Engine<br/>사업 설명서',M,167,CW,35,48,bold=True)
para('공정 데이터 분석과 품질 조사 업무를 통합하는<br/>MES 연동형 제조 AI 엔진',M,292,CW,16.5,26)
line(376)
txt('제품',M,402,9.4,True,BRAND)
para('제조 AI 엔진 + 공정 템플릿 + MES 위젯',M,425,CW,17,25,bold=True)
txt('사업 모델',M,481,9.4,True,BRAND)
para('제조기업 직접 공급과 MES·SI 파트너 공급을 함께 추진하는<br/>사업장 단위 소프트웨어 라이선스 사업',M,505,CW,12.1,20)
rect(M,598,CW,103)
txt('현재 제품 단계',M+17,614,10,True,BRAND)
para('로컬 엔진·MES 위젯 통합 및 배포 검증 완료<br/>공정별 분석 모델과 현장 연동 패키지 구축',M+17,640,CW-34,12,20,bold=True)
txt('2026.11.12 개정 · 현행 제품 구현 및 평가 기준',M,742,9,False,MUTED)
txt('MILGAICEO@gmail.com',M,769,10.5,True,BRAND)

# 02 — executive
y=header(2,'사업 개요','공정 분석과 품질 조사 업무의 통합',
 '기존 MES 환경에 AI 분석 기능을 연동하여 작업별 측정 근거와 담당자 처리 이력을 통합 관리한다.','E1, E3, E4')
for i,(v,t,s) in enumerate([('16','등록 모델·분석 경로','공정·데이터 출처별 분석 구성'),('118','엔진 시험 통과','엔진 기능·연동 검증 기준'),('0','외부 통신 요청','로컬 MES 통합 시연 시험 기준')]):
 x=M+i*(CW+12)/3;w=(CW-24)/3;rect(x,y,w,105);txt(v,x+14,y+10,31,True,BRAND);txt(t,x+14,y+56,9.2,True);para(s,x+14,y+77,w-28,8.2,12,fill=MUTED)
y+=130
y=table(['항목','사업 방향'],[
 ['고객 과제','공정 기록·검사 결과·조사 이력의 분산에 따른 이상 작업 확인 및 분석 근거 수집 업무의 반복.'],
 ['핵심 제품','기존 MES에 작업별 참고 분석, 측정 근거, 조사 등록·보류 기능을 제공하는 엔진·위젯 패키지.'],
 ['초기 공급 범위','단일 공정·설비군·입력 규격을 대상으로 한 유료 현장 검증.'],
 ['수익 구조','데이터 진단·초기 연동 매출과 사업장 단위 연간 사용권 매출의 결합.'],
 ['확장 전략','검증된 칼럼 매핑·템플릿·운영 절차의 재사용을 통한 설비 및 파트너 고객 확대.'],
 ],[1,4],y)
y=callout('핵심 개발 성과',
 '작업 선택, 등록 모델 추론, 측정 근거 제공 및 조사 이력 저장까지의 통합 기능을 구현하였다. 기존 MES를 유지하면서 AI 분석 기능을 도입할 수 있는 제품 구조와 배포 패키지를 확보하였다.',y,98)
para('공통 엔진과 공정별 템플릿을 기반으로 제조기업 직접 공급 및 MES·SI 파트너 공급을 추진한다.',M,y,CW,9.2,14,fill=MUTED)

# 03 — problem
y=header(3,'고객 과제 및 목표 시장','품질 조사 업무의 효율화를 통한 도입 가치',
 '분석 가능한 설비·검사 데이터를 보유한 제조기업을 초기 목표 고객으로 설정한다.')
y=table(['고객의 업무 과제','제공 기능 및 개선 방향'],[
 ['다수 화면·파일의 대조 작업','작업·LOT 단위 측정값과 분석 근거의 통합 조회.'],
 ['과다 알림에 따른 우선순위 불명확','고객과 합의한 알림 예산 적용 및 유효 사건 기록 기반 임계값 보정.'],
 ['분석 결과와 조치 이력의 분리','조사 등록·보류와 작업 ID의 연결 및 담당자 처리 이력 저장.'],
 ['설비·품목 조건별 비교 기준 불일치','입력 단위·작업 경계·공정 템플릿·비교 기준의 명시적 관리.'],
 ],[1.4,3.6],y)
y=label('초기 목표 고객의 선정 기준',y+5)
y=bullets(['작업 ID 또는 LOT와 연결되는 설비 기록·검사 결과를 확보할 수 있는 제조기업.',
 '반복 생산 공정을 운영하며 품질·생산기술 담당자의 실증 참여가 가능한 제조기업.',
 '기존 MES를 운영하며 분석 기능의 추가 도입을 검토하는 기업 또는 해당 MES 공급사.'],y)
y=label('초기 시장 진입 범위',y+10)
y=para('<b>MCT 작업별 분석을 대표 시연 모델로 활용</b>한다. 초기 유료 검증 대상은 고객 데이터의 준비도와 도입 목적에 따라 선정하며, 압입·열처리·사출 등은 판정 목표와 독립 검사 결과의 확보 수준에 따라 적용 범위를 설정한다.',M,y)
y=callout('핵심 고객 가치',
 '조사 대상 작업의 선정, 확인이 필요한 측정 근거, 담당자 검토 이력을 통합한다. 도입 효과는 조사 소요 시간의 변화와 현장 담당자가 확인한 유효 사건을 기준으로 평가한다.',y+22,96)

# 04 — actual screenshot
y=header(4,'제품 구성 및 사용 절차','기존 MES에 통합되는 MILG 분석 위젯',
 '공구·LOT·가공 회차별 측정값을 등록 모델에 입력하여 분석 결과와 검토 근거를 제공한다.','E4')
screenshot=ROOT/'ai/reports/delivery-mes/mes-widget-live.png'
im=Image.open(screenshot)
im.crop((235,185,1080,650)).save(TMP/'mes-selection.png')
im.crop((1092,350,1518,934)).save(TMP/'widget-detail.png')
lw=244;rw=245;gap=18
txt('MES · 작업 선택',M,y,10.8,True,BRAND)
image(TMP/'mes-selection.png',M,y+24,lw)
right=M+lw+gap
txt('MILG | AI · 실제 추론 결과',right,y,10.8,True,BRAND)
bottom=image(TMP/'widget-detail.png',right,y+24,rw)
yy=y+179
yy=bullets(['선택 작업의 센서 6개 요약값 전송',
 '현재 등록된 MCT 모델로 추론',
 '측정 근거와 참고 점수 표시',
 '조사 등록·보류 및 이력 저장'],yy,M,lw,10.1,11)
para('화면: 실제 로컬 실행 결과.<br/>데이터: 공개 NASA MCT 기록 재생.<br/>날짜·LOT 번호는 시연용으로 이동.',M,yy+4,lw,8.8,14,fill=MUTED)
y=max(bottom,yy+70)+24
y=callout('분석에서 현장 업무까지 이어지는 사용자 경험',
 '담당자는 선택 작업의 분석 점수와 측정 근거를 확인하고, 동일 화면에서 조사 등록·보류를 수행한다. 처리 이력은 작업 ID에 연결되어 품질 검토 및 후속 업무에 활용된다.',y,97)
para('작업별 데이터와 분석 결과를 일치시키는 요청 관리 기능을 통해 담당자의 검토 흐름을 일관되게 유지한다.',M,y,CW,9.2,14,fill=MUTED)

# 05 — model architecture
y=header(5,'엔진과 모델 구조','공통 코어와 공정별 모델의 결합',
 '데이터 입력 규격, 특징 계산, 학습, 모델 등록 및 추론을 통합 관리하는 소프트웨어 구조이다.','E1, E2')
blocks=[('입력 규격 및 공정 템플릿','작업 단위, 센서 칼럼·단위, 분석 구간·특징 계산, 설비·품목별 적용 조건 설정.'),
 ('모델 학습 및 평가','MILG 자체 누적 통계 모델과 공정별 분류·회귀·정상 기준 모델의 목적별 학습·평가.'),
 ('모델 선정 및 배포','모델·전처리·임계값·버전의 패키지화 및 평가 기준을 충족한 후보 모델 등록.'),
 ('현장 추론 및 결과 제공','작업 데이터에 대한 공정별 모델 추론 및 분석 점수·측정 근거 제공.')]
for i,(title,body) in enumerate(blocks):
 rect(M,y,CW,70);txt(f'0{i+1}',M+14,y+20,16,True,BRAND);txt(title,M+57,y+11,11.2,True);para(body,M+57,y+33,CW-74,9.8,15);y+=81
y=label('공통 코어의 학습 방식',y+2)
y=para('자체 누적 통계 모델은 의미별 특징의 공통 효과와 공정별 보정값을 학습한다. 공정별 성능 평가에 따라 전용 모델 및 기준 모델과 비교하여 적용 경로를 선정하며, 기존 공정의 성능 유지 여부를 함께 확인한다.',M,y)
y=callout('기업별 최적화의 원칙',
 '기업별 저장소·정상 기준·현장 학습 모델을 독립적으로 운영한다. 독립 검사로 확인된 데이터를 활용한 후보 학습·평가와 버전 관리를 통해 고객별 공정 특성에 맞는 모델 적용을 지원한다.',y+20,100)
para('공통 코어의 재사용성과 공정별 전용 모델의 전문성을 결합하여 다양한 제조 데이터에 대응하는 분석 구조를 구성한다.',M,y,CW,9,14,fill=MUTED)

# 06 — process scope
y=header(6,'공정별 분석 포트폴리오','7개 공정에 대응하는 모델·템플릿 구성',
 '공정의 물리적 특성과 분석 목적에 따라 분류·회귀·정상 기준 모델을 구성한다.','E1')
y=table(['공정','구현된 분석 기능','주요 활용 업무'],[
 ['MCT·절삭','공구 마모 기준 도달 징후 분석 및 작업별 센서 요약값 기반 MES 위젯 연동.','공구 점검 대상 선정, 가공 상태 검토, 작업별 근거 확인.'],
 ['압입 조립','힘-변위 곡선 특징 계산 및 확인된 정상 작업 대비 이탈 분석.','작업 곡선 비교, 정상 기준 관리, 조립 상태 검토.'],
 ['열처리','소재·처리 조건 기반 템퍼링 후 경도(HRC) 예측.','강종·처리 조건별 경도 검토 및 검사 계획 지원.'],
 ['사출','SCATIM 자료 기반 성형품 중량 예측 모델.','성형품 중량 추이 검토 및 공정 조건과의 관계 분석.'],
 ['볼트·나사 체결','체결 곡선 특징 기반 이상 분류 및 설비 NOK 판정 분석.','체결 상태 확인, 작업 조건 검토, 점검 대상 선정.'],
 ['프레스·성형','펌프 상태 이상 탐지, P2S 성형 분류, 시뮬레이션 기반 두께 예측.','설비 상태·성형 상태 분석 및 시뮬레이션 결과 검토.'],
 ['용접','점용접 품질 분류 및 너깃 직경 예측 모델.','용접 조건별 품질 검토 및 검사 대상 선정 지원.'],
 ],[.85,2.4,2.1],y,size=9.1)
y=callout('공정별 전문성과 공통 운영 체계의 결합',
 '16개 등록 경로를 공통 엔진에서 관리하고, 공정·데이터 출처별 목적에 적합한 모델을 선택한다. MCT 대표 시연은 기존 MES의 작업 선택부터 실제 추론·조사 저장까지의 통합 사용 흐름을 제공한다.',y+4,101)

# 07 — engineering validation
y=header(7,'제품 통합 검증 성과','제품 전반의 통합 검증 체계',
 '공개 데이터 예시와 독립 실행 환경을 활용하여 엔진 기능과 통합 사용 흐름을 검증하였다.','E1, E3, E4, E5')
y=table(['검증 항목','확인 성과','제품 적용 가치'],[
 ['엔진 기능 시험','118건 통과. 입력·모델 처리 및 운영 동작 확인.','동일한 동작을 반복 확인할 수 있는 회귀 검증 체계 확보.'],
 ['등록 경로 호출','16개 모델·분석 경로의 예시 입력 추론 확인.','공정별 분석 기능을 공통 실행 구조에서 관리.'],
 ['MES 통합','선택 작업의 센서값 일치, 실제 추론 및 조사 기록 저장 확인.','작업 조회부터 AI 분석·담당자 처리까지 통합 제공.'],
 ['통신·요청 처리','통합 시험에서 동시 요청 5개를 포함한 25건 호출 확인.','작업별 요청·응답 일치 및 복수 요청 처리 확인.'],
 ['독립 실행','기존 개발 환경과 분리한 가상환경에서 배포 패키지 실행.','설치 환경의 재현성과 배포 절차 확인.'],
 ['로컬 실행','MES 통합 시연 시험에서 외부 통신 요청 0건 확인.','외부 AI API에 의존하지 않는 로컬 추론 흐름 구현.'],
 ],[1.1,2.1,2.2],y,size=9.2)
y=label('분석 모델과 제품 운영을 연결한 개발 성과',y+5)
y=bullets(['모델 학습 결과를 공정별로 등록하고 실제 요청에 응답하는 실행 체계 구축.',
 'MES 작업 선택값과 모델 입력을 일치시키는 데이터 전달 및 응답 관리 구현.',
 '백업·복구·재시작과 담당자 조사 이력을 포함한 제품 운영 절차 확보.'],y,size=9.8)
y=callout('재현 가능한 시연 및 도입 기반',
 '코드·모델·전처리·입력 규격·의존성을 패키지로 구성하여 동일한 제품 흐름을 재현한다. 파트너의 기술 검토와 제조기업의 도입 검증에 활용할 수 있는 실행 자료를 제공한다.',y+15,98)

# 08 — reliability
y=header(8,'기업별 데이터 및 모델 운영','기업별 최적화와 안정적인 버전 관리',
 '평가 기준에 따른 모델 등록 통제와 입력 검증·버전 관리·복구 기능을 통합 제공한다.','E2, E3, E4, E5')
y=table(['영역','구현 및 검증 현황'],[
 ['평가 구간 보호','시험 구간을 먼저 고정. 학습 기준·임계값 보정·시험 분리. 공구·시간 경계 유지.'],
 ['입력 품질 관리','필수 센서값·작업 ID·응답 일치 여부 확인 및 작업 변경에 따른 분석 상태 관리.'],
 ['등록·복구','모델·전처리·임계값·템플릿의 통합 저장, 체크섬 확인 및 활성 버전 복구.'],
 ['업무 기록','작업 ID별 분석 결과 및 담당자 처리 이력 저장. 독립 검사 결과 기반 학습 절차 운영.'],
 ['기업별 운영','기업별 상태 디렉터리·프로세스·토큰·현장 모델의 분리 및 독립 운영.'],
 ['패키지','현재 Windows·Python 환경용 오프라인 의존성 포함. 새 가상환경에서 설치본과 MES 연동 확인.'],
 ],[1,4],y)
y=callout('고객 환경에 맞춘 독립 운영',
 '고객별 데이터 저장소와 학습 모델을 분리하고, 해당 기업의 검사 결과와 공정 조건에 기반한 후보 모델을 운영한다. 표준 엔진의 재사용성과 기업별 적용 유연성을 함께 확보하는 구조이다.',y+3,105)
para('현재 배포 패키지는 Windows·Python 환경을 기준으로 구성되어 있으며, 오프라인 설치용 의존성과 실행·복구 안내서를 포함한다.',M,y,CW,9.3,15,fill=MUTED)

# 09 — what we sell
y=header(9,'제품 구성 및 공급 범위','엔진 사용권과 현장 연동 패키지',
 '사업장·공정·설비 단위의 사용 범위를 설정하고, 공통 코어와 검증된 템플릿을 재사용한다.')
y=table(['상품','구성·제공 가치','주 고객'],[
 ['MILG Engine<br/>연간 사용권','공정별 모델 선택, 입력 계약, 전처리·추론, 평가·버전 관리, 작업 분석 API.','제조기업 / MES·SI'],
 ['MILG | AI Widget<br/>기본 UI 모듈','기존 MES의 선택 작업을 전달받아 점수·측정 근거·조사 등록·보류를 표시. 엔진 사용권에 기본 포함.','제조기업 / MES·SI'],
 ['공정 템플릿·모델<br/>적용 패키지','신호·단위·작업 경계·특징·비교 기준을 정하고, 해당 목적의 승인 모델과 함께 구성.','선택 공정 도입 고객'],
 ['초기 연결·배포<br/>일회성 서비스','CSV/API 매핑, 준비도 진단, 설치·백업·복구 설정, 검증 보고서와 담당자 교육.','신규 사업장'],
 ['파트너 API 공급<br/>공동 판매 방식','파트너의 자체 화면 또는 기본 위젯에 엔진을 연결. 사업장별 활성 사용권으로 계약.','MES·SI·설비 SW 기업'],
 ],[1.25,3.1,1.1],y)
y=label('기본 납품 범위',y+4)
y=para('입력 규격 및 칼럼 매핑표, 공정 템플릿·모델 버전, 위젯 또는 API 연동 예시, 평가 보고서, 설치·복구 안내서, 조사 이력 관리 기능을 기본 납품 범위로 설정한다.',M,y)
y=callout('소프트웨어 사용권 및 데이터 처리 조건',
 '사용 기간·사업장·공정·설비 수·업데이트·지원 범위를 명시한 사용권을 공급한다. 고객 데이터와 현장별 모델의 운영 조건을 계약에 반영하고, 동일 제품을 사업장 단위로 반복 공급하는 구조를 채택한다.',y+20,111)

# 10 — direct
y=header(10,'제조기업 직접 판매','데이터 진단에서 유료 검증·운영 계약까지',
 '도입 목적과 검증 기준을 사전 합의하고, 적용 범위 변경에 따른 대가와 일정은 변경 계약으로 관리한다.')
y=table(['단계','수행 및 납품 범위','단계별 전환 요건'],[
 ['01 사전 적합성 확인','담당자·문제·설비·입력·독립 검사 결과·권한·판정 시점을 확인.','입력과 목표가 실제로 연결되는지 확인.'],
 ['02 데이터 진단','파일 구조·단위·누락·중복·작업 경계·정답 시점을 점검하고 보완표 납품.','필수 데이터 확보 및 사용 권한 확인.'],
 ['03 유료 현장 검증','한 공정·한 설비군에서 기존 방식과 비교. 임계값은 검증 구간에서 결정.','사전에 합의한 성능·업무량·운영 기준 통과.'],
 ['04 운영 계약','사업장 사용권, 초기 연동, 지원 범위와 책임을 계약. 버전과 평가 결과를 인수 기준으로 고정.','고객 승인 후 참고 분석 범위에서 운영 시작.'],
 ['05 반복 확장','동일 템플릿의 설비·품목 확대 적용 및 추가 범위의 성능 재검증.','설비 확대에 따른 연동·지원 원가 및 재사용 효과 확인.'],
 ],[1.25,2.6,1.65],y)
y=label('유료 검증의 기본 계약 범위 · 제안',y+2)
y=bullets(['공정 1개·설비 1대·입력 규격 1개를 기본 범위로 설정. 데이터 준비 완료 후 4주를 기본 검증 기간으로 협의.',
 '고객 담당자와 작업 ID·독립 검사·알림 허용량·평가 구간을 검증 시작 전에 확정.',
 '범위 밖 설비 연결·새 신호·추가 화면·데이터 정리는 변경 계약으로 처리.'],y,size=9.8)
para('검증 기간과 공급 범위는 고객별 데이터 준비도 및 계약 조건을 기준으로 협의하여 확정한다.',M,y+6,CW,9.2,14,fill=MUTED)

# 11 — partner
y=header(11,'MES·SI 파트너 판매','MES·SI 파트너와 함께 공급하는 구조',
 '초기 사업장별 공동 공급을 추진하고, 반복 도입 실적을 기반으로 OEM 공급 체계를 확대할 계획이다.')
y=table(['구분','MES·SI 파트너','MILG'],[
 ['영업·고객 창구','기존 고객 발굴, MES 계약·현장 요구 수렴.','분석 적합성 확인, 기술 제안·견적 지원.'],
 ['데이터 연결','센서/DB 접근, 작업·LOT·검사 이력 연결, 칼럼 매핑.','입력 계약·템플릿 제공, 매핑 진단과 모델 규격 확인.'],
 ['화면','기존 MES UI 유지. 기본 위젯 탑재 또는 자체 UI 개발.','위젯·추론 API·연동 예시 제공.'],
 ['학습·평가','고객의 검사 결과와 공정 담당자 협조 확보.','후보 학습·평가·버전 승인·복구 지원.'],
 ['운영 지원','계약에 정한 1차 문의·현장 대응.','엔진 오류·모델 버전·분석 기능의 2차 지원.'],
 ],[1,2.2,2.2],y)
y=callout('파트너 대상 제공 가치',
 'AI 개발·모델 운영의 반복 업무 부담을 완화하고, 기존 MES 고객 대상의 추가 분석 상품 공급을 지원한다. 업무 시간 절감 및 추가 매출 효과는 초기 공동 도입을 통해 검증할 계획이다.',y+3,94)
y=label('파트너 거래 구조 · 제안',y)
y=bullets(['초기 파트너 공통 연회비는 부과하지 않으며, 유료 활성화 사업장별 라이선스를 기본 과금 단위로 설정한다.',
 '현장 연동비와 MILG 엔진 사용료를 구분하여 견적을 구성하고, 최종 고객 청구 및 지원 창구는 계약으로 확정한다.',
 'OEM·자체 브랜드 공급은 재배포 권한·공급 물량·지원 범위·업데이트 조건을 포함한 별도 계약 구조로 추진한다.'],y,size=9.8,gap=8)

# 12 — pricing
y=header(12,'가격 정책 및 수익 구조','사업장 단위 라이선스 기반 반복 매출',
 '사업장별 연간 사용료와 초기 연동비를 결합한다. 공급 단가는 초기 제안 기준으로, 계약 범위에 따라 협의한다.')
y=table(['항목','공급 단가 검토안<br/>(부가세 별도)','기본 공급 범위 · 제안'],[
 ['데이터 준비도 진단','100만 원 / 건','합의한 파일·입력 규격 1개. 진단·보완표 납품.'],
 ['유료 현장 검증','600만 원 / 건','1공정·1설비·1입력 규격. 4주 기본 협의.'],
 ['초기 연동·배포','500만 원 / 사업장','1공정·최대 3설비·상태 저장소 1개. 표준 연결 기준.'],
 ['운영 라이선스','1,200만 원 / 사업장·년','엔진·기본 위젯·승인 템플릿, 표준 업데이트·월 2시간 원격 지원 제안.'],
 ['파트너 공급가','900만 원 / 사업장·년','동일 기본 사용 범위. 파트너가 1차 지원을 담당하는 조건.'],
 ['추가 공정·비표준 연결','별도 견적','신호·설비·고객 UI·장애 대응 범위에 따라 산정.'],
 ],[1.3,1.6,2.7],y,size=9.1)
y=para('<b>직접 판매 기준 산정 예시:</b> 초기 연동비 500만 원과 연간 사용료 1,200만 원을 합산한 첫 운영연도 공급 금액은 1,700만 원이다. 진단·유료 검증은 별도 계약 시 추가하며, 차년도 기본 사용료는 1,200만 원으로 설정한다. 적용 범위 확대·추가 지원은 별도 산정한다.',M,y,CW,9.7,15)
y=para('<b>파트너 공급 기준 산정 예시:</b> 기준 판매가 1,200만 원과 공급가 900만 원의 차액은 300만 원으로, 판매가의 25%에 해당한다. 파트너의 실제 손익은 해당 차액에서 영업·지원·현장 대응 비용을 차감하여 산정한다.',M,y+16,CW,9.7,15)
y=callout('사업장별 수익성 관리 기준',
 '사업장별 매출과 연동·검증·지원·배포 원가를 구분 관리하고, 공급 단가·지원 시간·설비 범위를 연계 조정한다. 핵심 R&D·영업·법인 운영비를 포함한 회사 손익은 별도 산정하며, 매출 및 투자 계획은 계약 실적에 따라 갱신한다.',y+22,106)

# 13 — product strengths
y=header(13,'제품 경쟁력','MES 연동과 공정 전문성의 결합',
 'MES 연동, 공정별 분석, 기업별 운영 및 파트너 공급을 하나의 제품 체계로 구성한다.','E1, E2, E4')
y=table(['핵심 강점','제품 구성','고객·파트너 제공 가치'],[
 ['기존 MES 연동','선택 작업을 전달받는 위젯과 추론 API.','기존 사용자 화면과 작업 흐름을 유지하면서 분석 기능 추가.'],
 ['공정별 모델 구성','분류·회귀·정상 기준 모델 및 공정 템플릿.','공정의 물리적 특성과 업무 목적에 맞춘 분석 기능 구성.'],
 ['기업별 독립 운영','기업별 데이터·정상 기준·현장 모델 분리.','고객 환경의 데이터 운영 방식과 공정 특성 반영.'],
 ['표준 제품의 재사용','공통 코어·입력 규격·설치·복구 패키지.','반복 도입 시 활용 가능한 표준 연결·운영 자료 제공.'],
 ['유연한 공급 방식','기본 위젯 탑재 또는 파트너 자체 UI 연동.','제조기업 직접 공급과 MES·SI 공동 공급에 대응.'],
 ],[1.1,2.05,2.25],y,size=9.3)
y=label('고객의 업무에 직접 연결되는 제품 가치',y+5)
y=bullets(['작업 선택부터 AI 분석·근거 조회·조사 등록까지 이어지는 일관된 사용 흐름.',
 '측정 데이터와 담당자 처리 기록을 작업 ID 기준으로 통합 관리.',
 '고객별 적용 경험을 템플릿·매핑·운영 절차로 체계화하여 후속 도입에 활용.'],y,size=9.8)
y=callout('제품과 채널의 동시 확장',
 '제조기업에는 공정 분석 기능을, MES·SI 파트너에는 추가 판매 가능한 AI 모듈을 제안한다. 공통 엔진의 제품화와 파트너 공급 체계를 결합하여 사업장 단위의 반복 판매를 추진한다.',y+15,98)

# 14 — roadmap
y=header(14,'사업화 추진 계획','단계별 실증 및 반복 공급 체계 구축',
 '고객 데이터 준비도와 계약 범위에 따라 단계별 수행 일정 및 완료 기준을 확정한다.')
y=table(['추진 단계','주요 수행 과제','완료 기준'],[
 ['현재 완료','로컬 엔진·모델 경로, MES 실제 위젯, CSV 입력 검사, 배포·복구 패키지.','등록 경로 호출 및 MES 통합 시험 통과.'],
 ['발표·영업 준비','MCT 대표 시연 동결, 상품 범위·가격 가설·입력 규격·평가표 정리.','동일 버전으로 재현되는 시연과 검토 가능한 제안서.'],
 ['상용 계약 준비','데이터 이용권·소프트웨어 고지·지원 범위·계약 주체·인증과 접근 구조 정비.','법인·계약·권한·배포 책임을 확정.'],
 ['초기 유료 검증','단일 공정의 독립 검사·기존 업무와 비교. 신규 기간·LOT를 시험 데이터로 확보.','고객과 합의한 검출·알림·업무 시간·운영 기준 충족 여부 확인.'],
 ['반복 공급','같은 규격의 두 번째 설비·고객에 재사용. 파트너 공급 조건 검증.','연동 시간·지원 비용을 줄이면서 연간 사용권 갱신 확인.'],
 ],[1.1,2.55,1.9],y)
y=label('현장 실증의 사전 합의 지표',y+5)
y=bullets(['분석 성능: 불량 검출률·정상 오경보·일일 알림 수·회귀 오차를 목적별로 평가.',
 '업무 효과: 같은 유형 사건의 조사 시간과 담당자가 확인한 유효 발견을 기록.',
 '운영 효과: 수신 누락·통신 장애·재시도·복구 시간과 버전 변경을 기록.',
 '사업 효과: 사업장별 연동·지원 투입 시간, 원가, 유료 전환·갱신 여부를 확인.'],y,size=9.8,gap=8)
para('해외 진출은 국내 반복 공급 실적 확보 후 기존 파트너의 해외 사업장을 우선 검토한다. 언어·시간대·측정 단위·설비 및 현지 계약 조건에 대한 적합성을 추가 검증한다.',M,y+8,CW,9.2,14,fill=MUTED)

# 15 — growth
y=header(15,'사업 확장 전략','설비·공정·파트너를 통한 단계적 확장',
 '공통 엔진과 축적되는 공정 템플릿을 기반으로 고객 사업장 및 공급 채널을 확대할 계획이다.')
y=table(['확장 축','추진 방향','매출 연결 구조'],[
 ['사업장 내 설비 확대','동일 공정의 템플릿과 입력 매핑을 활용한 추가 설비 적용.','설비 범위 확대 및 추가 연동 서비스.'],
 ['분석 공정 확대','고객의 품질·생산 업무 목적에 따른 추가 공정 모델 구성.','공정별 적용 패키지 및 라이선스 범위 확대.'],
 ['기업별 분석 고도화','독립 검사 결과를 반영한 현장 모델 학습·평가 및 운영.','지속적인 제품 활용과 연간 사용권 갱신.'],
 ['파트너 고객 확대','MES·SI의 기존 고객 사업장에 표준 AI 모듈 공동 제안.','사업장별 파트너 라이선스 공급.'],
 ['OEM 공급','파트너의 제품 UI·영업 체계와 결합하는 공급 구조 추진.','협의된 물량과 사용 범위 기반의 반복 공급.'],
 ['해외 사업장 확대','국내 파트너 및 제조기업의 해외 사업장을 통한 동반 진출 검토.','해외 사업장별 연동·라이선스 공급.'],
 ],[1.2,2.5,1.7],y,size=9.3)
y=callout('누적되는 제품 자산',
 '도입 과정에서 확보한 입력 규격·공정 템플릿·평가 절차·운영 경험을 제품 자산으로 축적한다. 기업별 모델의 독립성을 유지하면서 공통 기능과 연결 절차의 재사용 범위를 확대한다.',y+8,100)
y=label('성장 관리 지표',y)
y=bullets(['유료 활성 사업장 수와 사업장별 연간 반복 매출.',
 '템플릿·입력 매핑 재사용률 및 사업장별 연동 투입 시간.',
 '파트너별 공동 제안·도입 건수와 라이선스 갱신 실적.'],y,size=9.8)

# 16 — references and revision
y=header(16,'제품·사업 요약 및 문의','제조 데이터와 현장 업무의 연결',
 'MILG는 제조 AI 엔진·공정 템플릿·MES 위젯을 결합한 소프트웨어 사업을 추진한다.')
y=callout('제품 및 판매 전략 요약',
 '기존 MES에 작업별 분석 기능을 연동하고, 공정별 모델을 공통 엔진에서 운영한다. 제조기업 직접 공급과 MES·SI 파트너 공급을 병행하며, 초기 연동과 사업장 단위 연간 라이선스를 결합한 수익 구조를 추진한다.',y,109)
y=label('내부 구현·시험 근거',y)
internal=[
 '<b>E1.</b> 활성 로컬 release: process-packs-621e95714daa4212a7befd43a4ed7af9. 등록 경로 16개, 공정별 목적·저장 평가값.',
 '<b>E2.</b> 자체 학습 코어·공정별 모델 운영: ai/mlng_ai/model.py, scenario_runtime.py 및 milg_engine/engine.py.',
 '<b>E3.</b> Python 엔진 기능 시험 118건 통과. 시험 명령: python -X utf8 -m pytest ai/tests -q.',
 '<b>E4.</b> tests/mes-widget-live.test.js 및 ai/reports/delivery-mes/result.json. 실제 선택 센서 일치·조사 저장·오래된 응답 차단·외부 요청 0건.',
 '<b>E5.</b> delivery 검증 보고서 및 tests/delivery-live.test.js. 독립 저장소·재시작·백업·복구와 예시 입력 HTTP 호출 확인.'
]
y=bullets(internal,y,size=8.4,gap=5)
y=label('제품 공급 및 협력 문의',y+16)
y=para('제조기업의 공정 분석 도입, MES 위젯 연동, 엔진 API 공급 및 파트너 공동 판매를 협의한다. 적용 공정과 입력 데이터, 사업장·설비 범위에 따라 제품 구성을 제안한다.',M,y)
para('<b>MILG</b> · <link href="mailto:MILGAICEO@gmail.com" color="'+BRAND+'">MILGAICEO@gmail.com</link>',M,y+24,CW,14,22)

if PAGE!=16:raise ValueError(f'Unexpected page count: {PAGE}')
c.save()
ARCHIVE.mkdir(parents=True,exist_ok=True)
shutil.copyfile(PDF,ARCHIVE/PDF.name)
print(PDF)
print(ARCHIVE/PDF.name)
