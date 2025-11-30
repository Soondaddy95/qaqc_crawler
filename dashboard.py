# ============================================================
# [DASHBOARD] QA/QC 트랙 통합 관제 시스템 (TIL + 출석)
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
load_dotenv()
st.set_page_config(
    page_title="QA 4기 통합 관제탑",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 스타일링 (CSS)
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

# 2. 데이터 로드 함수 (두 개의 탭을 각각 로드)
@st.cache_data(ttl=60)
def load_all_data():
    try:
        json_file = "qaqc-pipeline.json"
        sheet_url = os.environ.get("TIL_SHEET_URL")
        
        if not sheet_url:
            return None, None

        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(json_file, scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_url(sheet_url)
        
        # TIL 데이터
        try:
            til_sheet = spreadsheet.sheet1 # 혹은 spreadsheet.worksheet("raw_til_submissions")
            til_data = til_sheet.get_all_records()
            df_til = pd.DataFrame(til_data)
        except:
            df_til = pd.DataFrame()

        # 출석 데이터
        try:
            att_sheet = spreadsheet.worksheet("raw_attendance_logs")
            att_data = att_sheet.get_all_records()
            df_att = pd.DataFrame(att_data)
        except:
            df_att = pd.DataFrame()
            
        return df_til, df_att

    except Exception as e:
        st.error(f"❌ 데이터 로드 실패: {e}")
        return pd.DataFrame(), pd.DataFrame()

def main():
    # --- 데이터 준비 ---
    df_til, df_att = load_all_data()
    
    # 날짜 통합 (두 시트의 날짜를 합쳐서 선택지 생성)
    all_dates = set()
    if not df_til.empty and '날짜' in df_til.columns:
        all_dates.update(df_til['날짜'].astype(str).unique())
    if not df_att.empty and '날짜' in df_att.columns:
        all_dates.update(df_att['날짜'].astype(str).unique())
    
    sorted_dates = sorted(list(all_dates), reverse=True)

    # --- 사이드바 ---
    with st.sidebar:
        st.title("🎛️ 컨트롤 패널")
        
        if not sorted_dates:
            st.warning("데이터가 없습니다.")
            selected_date = datetime.now().strftime("%Y-%m-%d")
        else:
            selected_date = st.selectbox("📅 날짜 선택", sorted_dates, index=0)
            
        st.divider()
        if st.button("🔄 새로고침", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        
        st.caption(f"Last Update: {datetime.now().strftime('%H:%M:%S')}")

    # --- 메인 헤더 ---
    st.title(f"🏢 QA 4기 운영 현황 ({selected_date})")
    
    # 탭 분리
    tab1, tab2 = st.tabs(["📝 TIL 제출 현황", "⏰ 출석 관리 현황"])

    # =================================================================
    # [TAB 1] TIL 대시보드
    # =================================================================
    with tab1:
        if df_til.empty:
            st.warning("TIL 데이터가 없습니다.")
        else:
            # 오늘 데이터 필터링
            today_til = df_til[df_til['날짜'] == selected_date].copy()
            
            if not today_til.empty:
                submit_mask = today_til['제출여부'].astype(str).str.contains('1|제출|완료')
                submit_cnt = len(today_til[submit_mask])
                miss_cnt = len(today_til) - submit_cnt
                rate = round((submit_cnt / len(today_til)) * 100, 1)

                # KPI
                c1, c2, c3 = st.columns(3)
                c1.metric("총원", f"{len(today_til)}명")
                c2.metric("제출", f"{submit_cnt}명", f"{rate}%")
                c3.metric("미제출", f"{miss_cnt}명", delta=f"-{miss_cnt}", delta_color="inverse")

                # 미제출자 명단
                if miss_cnt > 0:
                    miss_names = today_til[~submit_mask]['이름'].tolist()
                    st.error(f"🚨 **미제출자:** {', '.join(miss_names)}")
                else:
                    st.success("🎉 전원 제출 완료!")
                
                st.divider()
                
                # 차트 & 테이블
                col_l, col_r = st.columns([1, 2])
                with col_l:
                    fig = px.pie(names=['제출', '미제출'], values=[submit_cnt, miss_cnt], 
                                 color_discrete_sequence=['#00CC96', '#EF553B'], hole=0.4)
                    st.plotly_chart(fig, use_container_width=True)
                
                with col_r:
                    def highlight_til(s):
                        return ['background-color: #ffcdd2' if '0' in str(v) or '미제출' in str(v) else '' for v in s]
                    st.dataframe(today_til[['이름', '제출여부', '날짜']].style.apply(highlight_til, subset=['제출여부']), use_container_width=True)
            else:
                st.info(f"{selected_date}일자 TIL 데이터가 없습니다.")

    # =================================================================
    # [TAB 2] 출석 대시보드
    # =================================================================
    with tab2:
        if df_att.empty:
            st.warning("출석 데이터가 없습니다.")
        else:
            # 오늘 데이터 필터링
            today_att = df_att[df_att['날짜'] == selected_date].copy()
            
            if not today_att.empty:
                # 상태별 카운트 (점수 기반: 1=출석, 0.5=지각/조퇴, 0=결석)
                # 문자열로 들어올 수도 있으니 형변환 안전장치
                today_att['상태'] = pd.to_numeric(today_att['상태'], errors='coerce').fillna(0)
                
                present_cnt = len(today_att[today_att['상태'] == 1])
                issue_cnt = len(today_att[today_att['상태'] == 0.5]) # 지각/조퇴
                absent_cnt = len(today_att[today_att['상태'] == 0])
                
                # KPI
                ac1, ac2, ac3, ac4 = st.columns(4)
                ac1.metric("총원", f"{len(today_att)}명")
                ac2.metric("✅ 정상 출석", f"{present_cnt}명")
                ac3.metric("⚠️ 지각/조퇴", f"{issue_cnt}명", delta_color="off")
                ac4.metric("🚨 결석", f"{absent_cnt}명", delta_color="inverse")
                
                # 이슈 인원 명단 (지각/조퇴/결석)
                issues = today_att[today_att['상태'] < 1]
                if not issues.empty:
                    st.warning(f"📢 **관리 필요 인원 ({len(issues)}명)**")
                    st.dataframe(issues[['이름', '입실시간', '퇴실시간', '상태']], use_container_width=True)
                else:
                    st.success("🎉 전원 정상 출석!")
                
                st.divider()
                
                # 상세 테이블
                st.subheader("📋 상세 출결 로그")
                
                # 색상 하이라이팅 함수
                def highlight_att(row):
                    val = row['상태']
                    if val == 0: return ['background-color: #ffcdd2'] * len(row) # 결석(빨강)
                    elif val == 0.5: return ['background-color: #fff9c4'] * len(row) # 지각/조퇴(노랑)
                    return [''] * len(row)

                st.dataframe(
                    today_att[['이름', '입실시간', '퇴실시간', '상태']].style.apply(highlight_att, axis=1),
                    use_container_width=True,
                    height=500
                )
                
            else:
                st.info(f"{selected_date}일자 출석 데이터가 없습니다.")

if __name__ == "__main__":
    main()