# ============================================================
# [FINAL COMPLETE] GitHub Actions & Local Hybrid TIL 자동화 봇
# ============================================================

# 👇 [수집 날짜 설정]
# None = 자동(가장 최근 영업일), "2025-11-27" = 특정 날짜 강제 지정
TARGET_DATE_OVERRIDE = None 

import subprocess
import time
import os
import sys
import socket
import json
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv

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

# .env 파일 로드 (로컬 실행용)
load_dotenv() 

# ============================================================
# Config 설정 클래스
# ============================================================

class Config:
    """크롤링 설정 및 환경 구성"""
    
    # 🔒 [보안] URL은 환경변수에서 가져옴
    BACKOFFICE_URL = os.environ.get("BACKOFFICE_URL")
    if not BACKOFFICE_URL:
        raise ValueError("❌ [설정 오류] 'BACKOFFICE_URL' 환경변수가 없습니다.")

    COURSE_NAME = "QA 4기"
    COURSE_KEYWORDS = ["KDT", "QA", "4"]
    BATCH_NAME = "4회차"
    CATEGORY = "QA/QC"

    CHROME_DEBUG_PORT = 9222
    
    if sys.platform == "darwin":  # Mac Studio
        CHROME_APP_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    else:  # Linux (GitHub Actions)
        CHROME_APP_PATH = "/usr/bin/google-chrome"
        
    USER_DATA_DIR = "~/apm_profile"
    
    # 대기 시간 설정
    WAIT_TIMEOUT = 20           
    CHROME_LAUNCH_WAIT = 4      
    MENU_CLICK_WAIT = 1         
    PAGE_LOAD_WAIT = 2          
    SEARCH_WAIT = 3             
    DATA_COLLECTION_WAIT = 0.5  
    PAGE_NAVIGATION_WAIT = 2
    MODAL_WAIT = 0.8 

    # 공휴일 데이터
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
# 1. 날짜 계산기
# ============================================================

class DateCalculator:
    @staticmethod
    def get_target_date(config: Config) -> str:
        """가장 최근 영업일 계산"""
        cursor = datetime.now().date()
        cursor -= timedelta(days=1) # 어제부터 탐색
        while True:
            cursor_str = cursor.strftime("%Y-%m-%d")
            if cursor.weekday() >= 5: # 주말
                cursor -= timedelta(days=1)
                continue
            if cursor_str in config.HOLIDAYS_KR: # 공휴일
                print(f"🏖️ 공휴일 스킵: {cursor_str}")
                cursor -= timedelta(days=1)
                continue
            return cursor_str

# ============================================================
# 2. 브라우저 관리자 (헤드리스 + 위장 모드)
# ============================================================

class ChromeManager:
    @staticmethod
    def launch_chrome(config: Config):
        options = webdriver.ChromeOptions()
        
        # [중요] 서버 환경용 헤드리스 설정
        options.add_argument("--headless=new") 
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        
        # [핵심] User-Agent 위장 (구글 로그인 차단 우회)
        user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        options.add_argument(f"user-agent={user_agent}")
        
        # 봇 탐지 방지
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        print("🕵️‍♂️ 크롬 드라이버(Headless + 위장) 초기화 중...")
        
        try:
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
            # navigator.webdriver 속성 숨기기
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            return driver
        except Exception as e:
            print(f"❌ 크롬 실행 실패: {e}")
            sys.exit(1)

# ============================================================
# 3. 크롤러 (로그인 + 옵션선택 + 상세수집)
# ============================================================

class BackOfficeCrawler:
    def __init__(self, driver, config: Config):
        self.driver = driver
        self.config = config
        self.wait = WebDriverWait(driver, config.WAIT_TIMEOUT)
    
    def force_click(self, element):
        try: element.click()
        except: self.driver.execute_script("arguments[0].click();", element)

    def handle_alert(self):
        """경고창 처리"""
        try:
            alert = self.driver.switch_to.alert
            print(f"⚠️ 경고창 발견: {alert.text}")
            alert.accept()
            time.sleep(1)
        except: pass

    def select_options(self):
        """옵션(카테고리/코스/기수) 선택 로직"""
        print("👉 옵션 선택 중...")
        try:
            # 1. 카테고리
            cat_xpath = f"//*[contains(text(), '{self.config.CATEGORY}')]"
            cat_elem = self.wait.until(EC.element_to_be_clickable((By.XPATH, cat_xpath)))
            self.force_click(cat_elem)
            time.sleep(self.config.MENU_CLICK_WAIT)
            
            # 2. 코스
            dropdowns = self.driver.find_elements(By.CSS_SELECTOR, ".ant-select-selector")
            if dropdowns:
                self.force_click(dropdowns[0])
                time.sleep(1)
                cond = " and ".join([f"contains(., '{k}')" for k in self.config.COURSE_KEYWORDS])
                opt = self.wait.until(EC.element_to_be_clickable((By.XPATH, f"//div[contains(@class, 'ant-select-item-option') and {cond}]")))
                self.force_click(opt)
                time.sleep(self.config.MENU_CLICK_WAIT)
            
            # 3. 기수
            dropdowns = self.driver.find_elements(By.CSS_SELECTOR, ".ant-select-selector")
            if len(dropdowns) >= 2:
                self.force_click(dropdowns[1])
                time.sleep(1)
                batch_opts = self.driver.find_elements(By.XPATH, f"//div[contains(@class, 'ant-select-item-option') and contains(., '{self.config.BATCH_NAME}')]")
                for opt in batch_opts:
                    if opt.is_displayed():
                        self.force_click(opt)
                        break
                time.sleep(self.config.MENU_CLICK_WAIT)
            
            print("✅ 옵션 선택 완료")
        except Exception as e:
            print(f"⚠️ 옵션 선택 중 이슈 (진행 시도): {e}")

    def navigate_and_search(self):
        print("\n🔗 백오피스 진입...")
        self.driver.get(self.config.BACKOFFICE_URL)
        
        # [쿠키 주입]
        cookies_json = os.environ.get("BACKOFFICE_COOKIES")
        if cookies_json:
            print("🍪 쿠키 주입 시도...")
            try:
                cookies = json.loads(cookies_json)
                for cookie in cookies:
                    if 'expiry' in cookie: del cookie['expiry']
                    if 'sameSite' in cookie: del cookie['sameSite']
                    try: self.driver.add_cookie(cookie)
                    except: pass
                self.driver.refresh()
                time.sleep(3)
                self.handle_alert()
            except Exception as e: print(f"⚠️ 쿠키 에러: {e}")
        
        # [메뉴 이동]
        try:
            time.sleep(2)
            menu_xpath = "//span[contains(text(), 'TIL 제출 현황 관리')]"
            menu = self.driver.find_elements(By.XPATH, menu_xpath)
            if not menu or not menu[0].is_displayed():
                op_menu = self.driver.find_element(By.XPATH, "//*[contains(text(), '내배캠 운영')]")
                self.force_click(op_menu)
                time.sleep(1)
            real_menu = self.wait.until(EC.element_to_be_clickable((By.XPATH, menu_xpath)))
            self.force_click(real_menu)
            time.sleep(2)
        except: pass
        
        # [옵션 선택 및 조회]
        self.select_options()
        
        try:
            search_btn = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., '조회하기')]")))
            self.force_click(search_btn)
            time.sleep(3)
            self.handle_alert()
        except: pass

    def collect_data(self, target_date: str) -> list:
        print(f"\n🐢 데이터 수집 시작 (타겟: {target_date})")
        total_data = []
        current_page = 1
        MAX_PAGES = 50
        
        while current_page <= MAX_PAGES:
            print(f"\n📄 [Page {current_page}] 스캔 중...")
            time.sleep(self.config.DATA_COLLECTION_WAIT)
            
            rows = self.driver.find_elements(By.CSS_SELECTOR, "tr.ant-table-row")
            if not rows:
                print("   ⚠️ 데이터 없음 (끝)")
                break
            
            row_count = len(rows)
            for i in range(row_count):
                try:
                    # DOM 리프레시 대응
                    current_row = self.driver.find_elements(By.CSS_SELECTOR, "tr.ant-table-row")[i]
                    name = current_row.find_elements(By.TAG_NAME, "td")[0].text.strip()
                    print(f"   🔍 ({i+1}/{row_count}) {name}님...", end="\r")
                    
                    btn = current_row.find_element(By.XPATH, ".//button[contains(., '제출 내역 보기') or span[contains(., '제출 내역 보기')]]")
                    self.force_click(btn)
                    
                    modal = self.wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".ant-modal-content")))
                    time.sleep(self.config.MODAL_WAIT)
                    
                    status = 0
                    found_date = False
                    modal_rows = modal.find_elements(By.CSS_SELECTOR, "tr.ant-table-row")
                    for m_row in modal_rows:
                        cols = m_row.find_elements(By.TAG_NAME, "td")
                        if not cols: continue
                        if cols[0].text.strip() == target_date:
                            status_txt = cols[1].text.strip()
                            if "미제출" in status_txt: status = 0
                            elif "제출" in status_txt or "완료" in status_txt: status = 1
                            else: status = 0
                            found_date = True
                            break
                    
                    close = modal.find_element(By.XPATH, ".//button[contains(., 'OK')]")
                    self.force_click(close)
                    self.wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, ".ant-modal-content")))
                    time.sleep(0.3)
                    total_data.append({"이름": name, "날짜": target_date, "제출여부": status})
                    
                except Exception as e:
                    print(f"\n   ❌ 에러: {e}")
                    try: webdriver.ActionChains(self.driver).send_keys(Keys.ESCAPE).perform(); time.sleep(1)
                    except: pass
                    continue
            
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
    config = Config()
    if manual_date:
        print(f"🛠️ [수동 모드] '{manual_date}' 기준 수집")
        target_date = manual_date
    else:
        print("🤖 [자동 모드] 날짜 계산 중...")
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
        print(f"❌ 에러: {e}")
        return pd.DataFrame()

# ============================================================
# 4. 구글 시트 업로더
# ============================================================

JSON_FILE = "qaqc-pipeline.json" 
TIL_SHEET_URL = os.environ.get("TIL_SHEET_URL")

class GoogleSheetManager:
    def __init__(self):
        if not TIL_SHEET_URL:
            raise ValueError("❌ 'TIL_SHEET_URL' 없음")
        self.scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        try:
            self.creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, self.scope)
            self.client = gspread.authorize(self.creds)
            self.sheet = self.client.open_by_url(TIL_SHEET_URL).sheet1
            print("✅ 구글 시트 연결 성공")
        except Exception as e:
            print(f"❌ 시트 연결 실패: {e}")
            raise e

    def save_data(self, new_df: pd.DataFrame):
        if new_df.empty:
            print("⚠️ 업로드할 데이터 없음")
            return
        target_date = new_df.iloc[0]['날짜']
        print(f"\n💾 저장 시작 ({target_date})...")
        try:
            existing_data = self.sheet.get_all_records()
            existing_df = pd.DataFrame(existing_data)
        except: existing_df = pd.DataFrame()

        if not existing_df.empty and '날짜' in existing_df.columns:
            existing_df['날짜'] = existing_df['날짜'].astype(str)
            existing_df = existing_df[existing_df['날짜'] != str(target_date)]

        final_df = pd.concat([new_df, existing_df], ignore_index=True)
        if '날짜' in final_df.columns:
            final_df = final_df.sort_values(by='날짜', ascending=False)
        final_df = final_df.fillna("") 

        self.sheet.clear()
        data_to_write = [final_df.columns.values.tolist()] + final_df.values.tolist()
        self.sheet.update(data_to_write)
        print(f"✅ 저장 완료!")

def upload_til_data(df: pd.DataFrame):
    try:
        if df is None or df.empty: return
        manager = GoogleSheetManager()
        manager.save_data(df)
    except Exception as e: print(f"❌ 업로드 오류: {e}")

# ============================================================
# 5. 메인 실행부 (반드시 존재해야 함!)
# ============================================================

if __name__ == "__main__":
    print("🔥 [START] 봇 가동 시작")
    
    # 1. 수집
    df_result = extract_til_data(manual_date=TARGET_DATE_OVERRIDE)
    
    # 2. 업로드
    if not df_result.empty:
        missed = len(df_result[df_result['제출여부'] == 0])
        print(f"📊 결과: 전체 {len(df_result)} / 미제출 {missed}")
        upload_til_data(df_result)
    else:
        print("⚠️ 수집된 데이터 없음")
        
    print("🏁 [END] 작업 종료")