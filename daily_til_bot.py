# ============================================================
# [FINAL v2] GitHub Actions용 TIL 봇 (쿠키 로그인 프리패스 탑재)
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
import json # json 처리용 모듈 추가
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

load_dotenv() 

# ============================================================
# Config
# ============================================================

class Config:
    BACKOFFICE_URL = os.environ.get("BACKOFFICE_URL")
    if not BACKOFFICE_URL:
        raise ValueError("❌ 'BACKOFFICE_URL' 환경변수가 없습니다.")

    COURSE_NAME = "QA 4기"
    COURSE_KEYWORDS = ["KDT", "QA", "4"]
    BATCH_NAME = "4회차"
    CATEGORY = "QA/QC"
    
    CHROME_DEBUG_PORT = 9222
    if sys.platform == "darwin":
        CHROME_APP_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    else:
        CHROME_APP_PATH = "/usr/bin/google-chrome"
        
    USER_DATA_DIR = "~/apm_profile"
    WAIT_TIMEOUT = 20           
    CHROME_LAUNCH_WAIT = 4      
    MENU_CLICK_WAIT = 1         
    PAGE_LOAD_WAIT = 2          
    SEARCH_WAIT = 3             
    DATA_COLLECTION_WAIT = 0.5  
    PAGE_NAVIGATION_WAIT = 2
    MODAL_WAIT = 0.8 

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
# Helper Classes
# ============================================================

class DateCalculator:
    @staticmethod
    def get_target_date(config: Config) -> str:
        cursor = datetime.now().date()
        cursor -= timedelta(days=1)
        while True:
            cursor_str = cursor.strftime("%Y-%m-%d")
            if cursor.weekday() >= 5:
                cursor -= timedelta(days=1)
                continue
            if cursor_str in config.HOLIDAYS_KR:
                print(f"🏖️ 공휴일 스킵: {cursor_str}")
                cursor -= timedelta(days=1)
                continue
            return cursor_str

# [수정본] ChromeManager (서버인 척 안 하고 맥북인 척 위장하기)

class ChromeManager:
    @staticmethod
    def launch_chrome(config: Config):
        options = webdriver.ChromeOptions()
        
        # 1. 헤드리스 모드 (서버니까 필수)
        options.add_argument("--headless=new") 
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        
        # 👇 [핵심] 가면 쓰기 (User-Agent 변조)
        # "나는 리눅스 서버가 아니라, 최신 맥북 크롬이다!" 라고 속임
        user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        options.add_argument(f"user-agent={user_agent}")
        
        # 봇 탐지 방지 옵션 추가
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        print("🕵️‍♂️ 크롬 드라이버(Headless + 위장 모드) 초기화 중...")
        
        try:
            driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()), 
                options=options
            )
            
            # [중요] navigator.webdriver 속성을 숨겨서 완벽하게 사람인 척 함
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            return driver
        except Exception as e:
            print(f"❌ 크롬 실행 실패: {e}")
            sys.exit(1)

# ============================================================
# Crawler (Updated with Cookie Logic)
# ============================================================

class BackOfficeCrawler:
    def __init__(self, driver, config: Config):
        self.driver = driver
        self.config = config
        self.wait = WebDriverWait(driver, config.WAIT_TIMEOUT)
    
    def force_click(self, element):
        try: element.click()
        except: self.driver.execute_script("arguments[0].click();", element)

    def navigate_and_search(self):
        print("\n🔗 백오피스 진입 중...")
        
        # 1. 사이트 접속 (로그인 안 된 상태)
        self.driver.get(self.config.BACKOFFICE_URL)
        
        # 2. [핵심] 쿠키 주입 (로그인 우회)
        cookies_json = os.environ.get("BACKOFFICE_COOKIES")
        
        if cookies_json:
            print("🍪 [서버 모드] 저장된 쿠키를 주입하여 로그인을 시도합니다.")
            try:
                cookies = json.loads(cookies_json)
                for cookie in cookies:
                    # Selenium 호환성을 위해 불필요한 키 삭제
                    if 'expiry' in cookie:
                        del cookie['expiry']
                    if 'sameSite' in cookie:
                        del cookie['sameSite'] # 가끔 충돌남
                        
                    try:
                        self.driver.add_cookie(cookie)
                    except Exception as e:
                        # 도메인 불일치 등 사소한 쿠키 에러는 무시
                        pass
                
                print("🔄 쿠키 주입 완료. 페이지를 새로고침합니다.")
                self.driver.refresh()
                time.sleep(3) # 새로고침 후 로그인 적용 대기
                
            except Exception as e:
                print(f"⚠️ 쿠키 적용 중 오류 (무시하고 진행): {e}")
        else:
            print("ℹ️ [로컬 모드] 기존 브라우저 세션을 사용합니다.")

        # 3. 메뉴 이동 (기존 로직)
        print("👉 메뉴 이동 시도...")
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
            print("✅ 메뉴 진입 성공")
        except Exception as e:
            print(f"⚠️ 메뉴 이동 실패 (로그인 실패 가능성): {e}")
            # 스크린샷 찍어서 디버깅 가능하게 (선택)
            # self.driver.save_screenshot("login_failed.png")
        
        time.sleep(2)
        
        try:
            search_btn = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., '조회하기')]")))
            self.force_click(search_btn)
            time.sleep(3)
        except: pass

    def collect_data(self, target_date: str) -> list:
        print(f"\n🐢 데이터 수집 시작 (타겟 날짜: {target_date})")
        total_data = []
        current_page = 1
        MAX_PAGES = 50
        
        while current_page <= MAX_PAGES:
            print(f"\n📄 [Page {current_page}] 스캔 중...", end="")
            time.sleep(self.config.DATA_COLLECTION_WAIT)
            
            rows = self.driver.find_elements(By.CSS_SELECTOR, "tr.ant-table-row")
            if not rows:
                print("\n   ⚠️ 데이터가 없습니다. (로그인이 풀렸거나 데이터 끝)")
                break
            
            row_count = len(rows)
            print(f" -> {row_count}명 발견")
            
            for i in range(row_count):
                try:
                    current_row = self.driver.find_elements(By.CSS_SELECTOR, "tr.ant-table-row")[i]
                    name = current_row.find_elements(By.TAG_NAME, "td")[0].text.strip()
                    print(f"   🔍 ({i+1}/{row_count}) {name}님 확인 중...", end="\r")
                    
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
        print(f"🛠️ [수동 모드] '{manual_date}' 수집")
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
# Uploader
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
            print(f"❌ 구글 시트 연결 실패: {e}")
            raise e

    def save_data(self, new_df: pd.DataFrame):
        if new_df.empty:
            print("⚠️ 데이터 없음")
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

if __name__ == "__main__":
    df_result = extract_til_data(manual_date=TARGET_DATE_OVERRIDE)
    if not df_result.empty:
        upload_til_data(df_result)