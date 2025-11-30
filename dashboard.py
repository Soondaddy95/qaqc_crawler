# ============================================================
# [DASHBOARD] QA/QC 트랙 TIL 관리 대시보드
# ============================================================

import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import plotly.express as px
import plotly.graph_objects as go
import os
from dotenv import load_dotenv
from datetime import datetime

# 1. 환경 설정 및 페이지 세팅
load_dotenv() # 로컬에서 .env 파일 읽기
st.set_page_config(
    page_title="QA 4기 TIL 관제탑",
    page_icon="🚁",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 스타일링 (CSS 커스텀)
st.markdown("""
    <style>
        .metric-card {
            background-color: #f0f2f6;
            border-radius: 10px;
            padding: 15px;
            text-align: center;
        }
        div[data-testid="stMetricValue"] {
            font-size: 28px;
            font-weight: bold;
        }
    </style>
""", unsafe_allow_html=True)

# 2. 데이터 로드 (캐싱으로 속도 10배 향상)
@st.cache_data(ttl=60)  # 60초마다 데이터 갱신 (서버 부하 감소)
def load_data():
    try:
        # 인증 파일 및 URL 로드
        json_file = "qaqc-pipeline.json"
        sheet_url = os.environ.get("TIL_SHEET_URL")
        
        if not sheet_url:
            st.error("❌ .env 파일에 'TIL_SHEET_URL'이 설정되지 않았습니다.")
            return pd.DataFrame()

        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(json_file, scope)
        client = gspread.authorize(creds)
        
        sheet = client.open_by_url(sheet_url).sheet1
        data = sheet.get_all_records()
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"❌ 데이터 로드 실패: {e}")
        st.error("💡 팁: 'qaqc-pipeline.json' 파일이 유효한지 확인해주세요.")
        return pd.DataFrame()

def main():
    # --- 데이터 로딩 및 검증 ---
    df = load_data()
    if df.empty:
        st.warning("⚠️ 아직 수집된 데이터가 없습니다. 크롤러(daily_til_bot.py)를 먼저 실행해주세요.")
        return

    # 날짜 필터 (최신순)
    if '날짜' not in df.columns:
        st.error("데이터에 '날짜' 컬럼이 없어 대시보드를 표시할 수 없습니다.")
        return

    # --- 사이드바 (컨트롤 패널) ---
    with st.sidebar:
        st.title("🎛️ 조회 옵션")
        
        df['날짜'] = df['날짜'].astype(str)
        date_list = sorted(df['날짜'].unique().tolist(), reverse=True)
        selected_date = st.selectbox("📅 날짜 선택", date_list, index=0)
            
        st.divider()
        if st.button("🔄 데이터 새로고침", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        
        st.caption(f"Last Check: {datetime.now().strftime('%H:%M:%S')}")

    # --- 데이터 전처리 (핵심) ---
    today_df = df[df['날짜'] == selected_date].copy()
    
    # 제출(1)/미제출(0) 판별 로직 적용
    submit_mask = today_df['제출여부'].astype(str).str.contains('1|제출|완료')
    submit_df = today_df[submit_mask]
    miss_df = today_df[~submit_mask]
    
    total_cnt = len(today_df)
    submit_cnt = len(submit_df)
    miss_cnt = len(miss_df)
    submit_rate = round((submit_cnt / total_cnt) * 100, 1) if total_cnt > 0 else 0

    # --- 메인 화면 ---
    st.title(f"🚁 QA 4기 TIL 현황 ({selected_date})")
    st.markdown("---")

    # [섹션 1] KPI 카드
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📅 기준일", selected_date)
    col2.metric("👥 총원", f"{total_cnt}명")
    col3.metric("✅ 제출", f"{submit_cnt}명", f"{submit_rate}%")
    col4.metric("🚨 미제출", f"{miss_cnt}명", delta=f"-{miss_cnt}명", delta_color="inverse")
    
    # [섹션 2] 미제출자 경보 시스템
    if miss_cnt > 0:
        st.error(f"📢 **오늘의 미제출자 ({miss_cnt}명)** 집중 관리 필요!")
        
        cols = st.columns(5)
        for idx, row in enumerate(miss_df.itertuples()):
            with cols[idx % 5]:
                st.warning(f"**{row.이름}**")
    else:
        st.success("🎉 **전원 제출 완료!** 완벽합니다.")

    st.markdown("---")

    # [섹션 3] 시각화 (차트)
    c1, c2 = st.columns([2, 1])

    with c1:
        st.subheader("📉 주간 제출율 추세")
        
        daily_grp = df.groupby('날짜').apply(
            lambda x: len(x[x['제출여부'].astype(str).str.contains('1|제출|완료')]) / len(x) * 100
        ).reset_index(name='제출율')
        
        daily_grp = daily_grp.sort_values('날짜').tail(7)

        fig_line = px.line(daily_grp, x='날짜', y='제출율', markers=True, text='제출율')
        fig_line.update_traces(line_color='#FF4B4B', line_width=3, texttemplate='%{text:.1f}%', textposition='top center')
        fig_line.update_layout(yaxis_range=[0, 110])
        st.plotly_chart(fig_line, use_container_width=True)

    with c2:
        st.subheader("🍰 금일 비중")
        
        fig_pie = go.Figure(data=[go.Pie(
            labels=['제출', '미제출'], 
            values=[submit_cnt, miss_cnt], 
            hole=.4,
            marker=dict(colors=['#00CC96', '#EF553B'])
        )])
        fig_pie.update_layout(showlegend=True, margin=dict(t=0, b=0, l=0, r=0))
        st.plotly_chart(fig_pie, use_container_width=True)

    # [섹션 4] 전체 명단 테이블
    with st.expander("📋 전체 수강생 상세 명단 보기 (클릭)"):
        # 미제출자 행 강조 스타일
        def highlight_row(row):
            val = str(row['제출여부'])
            if '0' in val or '미제출' in val:
                return ['background-color: #ffebee'] * len(row)
            return [''] * len(row)

        st.dataframe(
            today_df.style.apply(highlight_row, axis=1),
            use_container_width=True,
            height=500,
            hide_index=True
        )

if __name__ == "__main__":
    main()