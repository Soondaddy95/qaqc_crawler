# ============================================================
# [FINAL] GitHub Actions & Local Hybrid TIL 자동화 봇
# ============================================================

# 👇 [수집 날짜 설정]
# None으로 두면 시스템이 '가장 최근 영업일'을 자동 계산합니다.
# 특정 날짜를 수집하려면 "2025-11-27" 처럼 문자열로 적으세요.
TARGET_DATE_OVERRIDE = None 

import subprocess
import time
import os
import sys
import socket
import pandas as pd
from datetime import datetime, timedelta

# [환경변수 로드 (.env 파일 지원)]
from dotenv import load_dotenv
load_dotenv() 

# [Google Sheet & OAuth]
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# [Selenium Libraries]
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    WebDriverException, SessionNotCreatedException, TimeoutException, 
    NoSuchElementException, StaleElementReferenceException
)
from selenium.webdriver.common.keys import Keys

# ============================================================
# Config 설정 클래스 (보안 강화 및 OS 분기 처리)
# ============================================================

class Config:
    """크롤링 설정 및 환경 구성"""
    
    # 🔒 [보안] URL은 환경변수(.env 또는 GitHub Secrets)에서 가져옴
    BACKOFFICE_URL = os.environ.get("BACKOFFICE_URL")
    
    # URL 누락 시 안전장치 (에러 발생)
    if not BACKOFFICE_URL:
        raise ValueError("❌ [설정 오류] 'BACKOFFICE_URL' 환경변수가 없습니다. .env 파일이나 GitHub Secrets를 확인하세요.")

    COURSE_NAME = "QA 4기"
    COURSE_KEYWORDS = ["KDT", "QA", "4"]
    BATCH_NAME = "4회차"
    CATEGORY = "QA/QC"

    # === 크롬 설정 (OS별 자동 분기) ===
    CHROME_DEBUG_PORT = 9222
    
    if sys.platform == "darwin":  # Mac Studio (Local)
        CHROME_APP_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    else:  # Linux (GitHub Actions Server)
        CHROME_APP_PATH = "/usr/bin/google-chrome" # 리눅스 표준 경로
        
    USER_DATA_DIR = "~/apm_profile"
    
    # === 대기 시간 설정 ===
    WAIT_TIMEOUT = 20           
    CHROME_LAUNCH_WAIT = 4      
    MENU_CLICK_WAIT = 1         
    PAGE_LOAD_WAIT = 2          
    SEARCH_WAIT = 3             
    DATA_COLLECTION_WAIT = 0.5  
    PAGE_NAVIGATION_WAIT = 2
    MODAL_WAIT = 0.8  # 상세 모달 로딩 대기

    # === 공휴일 데이터 ===
    HOLIDAYS_KR = {
        "2025-01-01": "신정", "2025-01-27": "설날 연휴", "2025-01-28": "설날", 
        "2025-01-29": "설날 연휴", "2025-01-30": "설날 대체공휴일",
        "2025-03-01": "삼일절", "2025-03-03": "삼일절 대체공휴일", 
        "2025-05-05": "어린이날", "2025-05-06": "부처님오신날 대체공휴일",
        "2025-06-03": "대통령선거(임시)", "2025-06-06": "현충일", 
        "2025-08-15": "광복절", "2025-10-03": "개천절",
        "2025-10-05": "추석 연휴", "2025-10-06": "추석", "2025-10-07": "추석 연휴", 
        "2025-10-08": "추석 대체공휴일", "2025-10-09": "한글날", "2025-12-25": "크리스마스"
    }

# ============================================================
# 1. 날짜 계산기 (Date Calculator)
# ============================================================

class DateCalculator:
    @staticmethod
    def get_target_date(config: Config) -> str:
        """가장 최근 영업일(평일) 계산"""
        cursor = datetime.now().date()
        cursor -= timedelta(days=1) # 어제부터 탐색
        while True:
            cursor_str = cursor.strftime("%Y-%m-%d")
            # 주말 체크
            if cursor.weekday() >= 5:
                cursor -= timedelta(days=1)
                continue
            # 공휴일 체크
            if cursor_str in config.HOLIDAYS_KR:
                print(f"🏖️ 공휴일 스킵: {cursor_str}")
                cursor -= timedelta(days=1)
                continue
            return cursor_str

# ============================================================
# 2. 브라우저 관리자 (Chrome Manager)
# ============================================================

class ChromeManager:
    @staticmethod
    def launch_chrome(config: Config):
        """헤드리스 모드 지원 크롬 실행"""
        options = webdriver.ChromeOptions()
        
        # [중요] GitHub Actions 및 백그라운드 실행을 위한 헤드리스 설정
        options.add_argument("--headless=new") 
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        
        print("🕵️‍♂️ 크롬 드라이버(Headless) 초기화 중...")
        
        try:
            # webdriver_manager가 드라이버 자동 설치 및 관리
            driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()), 
                options=options
            )
            return driver
        except Exception as e:
            print(f"❌ 크롬 실행 실패: {e}")
            sys.exit(1)

# ============================================================
# 3. 크롤러 (Crawler Logic)
# ============================================================

class BackOfficeCrawler:
    def __init__(self, driver, config: Config):
        self.driver = driver
        self.config = config
        self.wait = WebDriverWait(driver, config.WAIT_TIMEOUT)
    
    def force_click(self, element):
        """JS 강제 클릭 (안정성 확보)"""
        try: element.click()
        except: self.driver.execute_script("arguments[0].click();", element)

    def navigate_and_search(self):
        """백오피스 진입 -> 메뉴 이동 -> 조회"""
        print("\n🔗 백오피스 진입 중...")
        if "h99backoffice" not in self.driver.current_url:
            self.driver.get(self.config.BACKOFFICE_URL)
            
        try:
            time.sleep(1)
            # 메뉴 찾기 (XPath)
            menu_xpath = "//span[contains(text(), 'TIL 제출 현황 관리')]"
            menu = self.driver.find_elements(By.XPATH, menu_xpath)
            
            # 메뉴 안 보이면 상위 메뉴 펼치기
            if not menu or not menu[0].is_displayed():
                op_menu = self.driver.find_element(By.XPATH, "//*[contains(text(), '내배캠 운영')]")
                self.force_click(op_menu)
                time.sleep(1)
            
            # 메뉴 클릭
            real_menu = self.wait.until(EC.element_to_be_clickable((By.XPATH, menu_xpath)))
            self.force_click(real_menu)
        except: pass
        
        time.sleep(2)
        
        try:
            # 조회 버튼 클릭 (옵션은 기본값 사용 가정)
            search_btn = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., '조회하기')]")))
            self.force_click(search_btn)
            time.sleep(3)
        except: pass

    def collect_data(self, target_date: str) -> list:
        """상세 모달 진입 방식 데이터 수집"""
        print(f"\n🐢 데이터 수집 시작 (타겟 날짜: {target_date})")
        print("ℹ️ 한 명씩 상세 내역을 확인합니다...")
        
        total_data = []
        current_page = 1
        MAX_PAGES = 50
        
        while current_page <= MAX_PAGES:
            print(f"\n📄 [Page {current_page}] 목록 스캔 중...")
            time.sleep(self.config.DATA_COLLECTION_WAIT)
            
            rows = self.driver.find_elements(By.CSS_SELECTOR, "tr.ant-table-row")
            if not rows:
                print("   ⚠️ 더 이상 데이터가 없습니다.")
                break
            
            row_count = len(rows)
            for i in range(row_count):
                try:
                    # DOM 리프레시 대응 (매번 새로 찾기)
                    current_row = self.driver.find_elements(By.CSS_SELECTOR, "tr.ant-table-row")[i]
                    name = current_row.find_elements(By.TAG_NAME, "td")[0].text.strip()
                    print(f"   🔍 ({i+1}/{row_count}) {name}님 확인 중...", end="\r")
                    
                    # [제출 내역 보기] 버튼 클릭
                    btn = current_row.find_element(By.XPATH, ".//button[contains(., '제출 내역 보기') or span[contains(., '제출 내역 보기')]]")
                    self.force_click(btn)
                    
                    # 모달 대기
                    modal = self.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".ant-modal-content")))
                    time.sleep(self.config.MODAL_WAIT)
                    
                    # --- [미제출 판별 핵심 로직] ---
                    status = 0
                    found_date = False
                    
                    modal_rows = modal.find_elements(By.CSS_SELECTOR, "tr.ant-table-row")
                    for m_row in modal_rows:
                        cols = m_row.find_elements(By.TAG_NAME, "td")
                        if not cols: continue
                        
                        # 날짜 매칭
                        if cols[0].text.strip() == target_date:
                            status_txt = cols[1].text.strip()
                            
                            # 텍스트 분석
                            if "미제출" in status_txt:
                                status = 0
                            elif "제출" in status_txt or "완료" in status_txt:
                                status = 1
                            else:
                                status = 0 # 모호하면 0
                                
                            found_date = True
                            break
                    
                    if not found_date:
                        status = 0 # 해당 날짜 행이 없으면 미제출
                    
                    # 모달 닫기
                    close = modal.find_element(By.XPATH, ".//button[contains(., 'OK')]")
                    self.force_click(close)
                    self.wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, ".ant-modal-content")))
                    time.sleep(0.3)
                    
                    total_data.append({"이름": name, "날짜": target_date, "제출여부": status})
                    
                except Exception as e:
                    print(f"\n   ❌ {name} 처리 중 에러: {e}")
                    # 에러 복구 (ESC)
                    try: webdriver.ActionChains(self.driver).send_keys(Keys.ESCAPE).perform(); time.sleep(1)
                    except: pass
                    continue
            
            # 페이지 넘김
            try:
                next_btns = self.driver.find_elements(By.CSS_SELECTOR, "li.ant-pagination-next")
                if next_btns and "ant-pagination-disabled" not in next_btns[0].get_attribute("class"):
                     self.force_click(next_btns[0])
                     current_page += 1
                     time.sleep(2)
                else: break
            except: break
            
        return total_data

def extract_til_data(manual_date: str = None) -> pd.DataFrame:
    """수집 실행 함수"""
    config = Config()
    
    if manual_date:
        print(f"🛠️ [수동 모드] '{manual_date}' 기준으로 수집합니다.")
        target_date = manual_date
    else:
        print("🤖 [자동 모드] 가장 최근 영업일을 계산합니다.")
        target_date = DateCalculator.get_target_date(config)
        
    driver = ChromeManager.launch_chrome(config)
    
    try:
        crawler = BackOfficeCrawler(driver, config)
        crawler.navigate_and_search()
        
        data = crawler.collect_data(target_date)
        
        df = pd.DataFrame(data)
        print(f"\n✅ 수집 완료! 총 {len(df)}건.")
        return df
        
    except Exception as e:
        print(f"❌ 에러 발생: {e}")
        return pd.DataFrame()


# ============================================================
# 4. 구글 시트 업로더 (Uploader)
# ============================================================

# 🔒 [보안] URL 환경변수 로드
JSON_FILE = "qaqc-pipeline.json" 
TIL_SHEET_URL = os.environ.get("TIL_SHEET_URL")

class GoogleSheetManager:
    def __init__(self):
        if not TIL_SHEET_URL:
            raise ValueError("❌ [설정 오류] 'TIL_SHEET_URL' 환경변수가 없습니다.")

        self.scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        try:
            self.creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, self.scope)
            self.client = gspread.authorize(self.creds)
            self.sheet = self.client.open_by_url(TIL_SHEET_URL).sheet1
            print("✅ 구글 시트 연결 성공")
        except Exception as e:
            print(f"❌ 구글 시트 연결 실패: {e}")
            raise e

    def save_data(self, new_df: pd.DataFrame):
        if new_df.empty:
            print("⚠️ 업로드할 데이터가 없습니다.")
            return

        target_date = new_df.iloc[0]['날짜']
        print(f"\n💾 시트 저장 시작... (타겟 날짜: {target_date})")
        
        try:
            existing_data = self.sheet.get_all_records()
            existing_df = pd.DataFrame(existing_data)
        except:
            existing_df = pd.DataFrame()

        # 중복 제거 (기존 데이터에서 타겟 날짜 행 삭제)
        if not existing_df.empty and '날짜' in existing_df.columns:
            existing_df['날짜'] = existing_df['날짜'].astype(str)
            existing_df = existing_df[existing_df['날짜'] != str(target_date)]

        # 병합 및 정렬
        final_df = pd.concat([new_df, existing_df], ignore_index=True)
        if '날짜' in final_df.columns:
            final_df = final_df.sort_values(by='날짜', ascending=False)
        final_df = final_df.fillna("") 

        # 업로드
        self.sheet.clear()
        data_to_write = [final_df.columns.values.tolist()] + final_df.values.tolist()
        self.sheet.update(data_to_write)
        print(f"✅ 저장 완료! 총 {len(final_df)}행 기록됨.")

def upload_til_data(df: pd.DataFrame):
    """업로드 실행 함수"""
    try:
        if df is None or df.empty:
            return
        manager = GoogleSheetManager()
        manager.save_data(df)
    except Exception as e:
        print(f"❌ 업로드 오류: {e}")


# ============================================================
# 5. 실행부 (Main Entry)
# ============================================================

if __name__ == "__main__":
    # 1. 데이터 수집
    df_result = extract_til_data(manual_date=TARGET_DATE_OVERRIDE)
    
    # 2. 결과 검증 및 업로드
    if not df_result.empty:
        # 간단한 결과 리포트
        missed = len(df_result[df_result['제출여부'] == 0])
        print(f"📊 [리포트] 전체: {len(df_result)}명 / 제출: {len(df_result)-missed} / 미제출: {missed}")
        
        upload_til_data(df_result)
    else:
        print("⚠️ 수집된 데이터가 없어 업로드를 종료합니다.")