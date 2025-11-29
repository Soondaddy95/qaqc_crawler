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

# ============================================================
# 3. 크롤러 (Crawler Logic) - 옵션 선택 기능 복구
# ============================================================

class BackOfficeCrawler:
    def __init__(self, driver, config: Config):
        self.driver = driver
        self.config = config
        self.wait = WebDriverWait(driver, config.WAIT_TIMEOUT)
    
    def force_click(self, element):
        """JS 강제 클릭"""
        try: element.click()
        except: self.driver.execute_script("arguments[0].click();", element)

    def handle_alert(self):
        """혹시 모를 경고창 처리"""
        try:
            alert = self.driver.switch_to.alert
            print(f"⚠️ 경고창 발견: {alert.text}")
            alert.accept()
            time.sleep(1)
        except:
            pass

    def select_options(self):
        """카테고리, 코스, 기수 선택 로직 (서버 환경 필수)"""
        print("👉 옵션 선택 중...")
        
        try:
            # 1. 카테고리 선택 (QA/QC)
            category_xpath = f"//*[contains(text(), '{self.config.CATEGORY}')]"
            category_elem = self.wait.until(EC.element_to_be_clickable((By.XPATH, category_xpath)))
            self.force_click(category_elem)
            time.sleep(self.config.MENU_CLICK_WAIT)
            
            # 2. 코스 선택 (드롭다운 열고 -> 키워드 찾기)
            # 드롭다운들이 .ant-select-selector 클래스를 가짐
            dropdowns = self.driver.find_elements(By.CSS_SELECTOR, ".ant-select-selector")
            if dropdowns:
                self.force_click(dropdowns[0]) # 첫 번째가 보통 코스 선택
                time.sleep(1)
                
                # 코스 키워드로 옵션 찾기
                course_conditions = " and ".join([f"contains(., '{k}')" for k in self.config.COURSE_KEYWORDS])
                course_xpath = f"//div[contains(@class, 'ant-select-item-option') and {course_conditions}]"
                course_opt = self.wait.until(EC.element_to_be_clickable((By.XPATH, course_xpath)))
                self.force_click(course_opt)
                time.sleep(self.config.MENU_CLICK_WAIT)
            
            # 3. 기수(회차) 선택
            # 코스를 선택하면 DOM이 바뀌므로 다시 찾음
            dropdowns = self.driver.find_elements(By.CSS_SELECTOR, ".ant-select-selector")
            if len(dropdowns) >= 2:
                self.force_click(dropdowns[1]) # 두 번째가 기수 선택
                time.sleep(1)
                
                batch_xpath = f"//div[contains(@class, 'ant-select-item-option') and contains(., '{self.config.BATCH_NAME}')]"
                batch_opts = self.driver.find_elements(By.XPATH, batch_xpath)
                for opt in batch_opts:
                    if opt.is_displayed():
                        self.force_click(opt)
                        break
                time.sleep(self.config.MENU_CLICK_WAIT)
                
            print("✅ 옵션 선택 완료")
            
        except Exception as e:
            print(f"⚠️ 옵션 선택 중 문제 발생 (이미 선택되어 있을 수 있음): {e}")
            # 에러 나도 일단 진행 (혹시 기본값이 맞을 수도 있으니)

    def navigate_and_search(self):
        """백오피스 진입 -> 쿠키 -> 메뉴 -> 옵션 -> 조회"""
        print("\n🔗 백오피스 진입 중...")
        if not self.config.BACKOFFICE_URL:
             raise ValueError("❌ 환경변수 'BACKOFFICE_URL' 없음")

        if "h99backoffice" not in self.driver.current_url:
            self.driver.get(self.config.BACKOFFICE_URL)
            
        # --- 쿠키 로직 ---
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
                self.handle_alert() # 리프레시 직후 알림창 뜰 경우 대비
            except Exception as e: print(f"⚠️ 쿠키 에러: {e}")
        
        # --- 메뉴 이동 ---
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
        
        # --- 👇 [추가] 옵션 선택 실행! ---
        self.select_options()
        
        # --- 조회 버튼 클릭 ---
        try:
            search_btn = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., '조회하기')]")))
            self.force_click(search_btn)
            time.sleep(3)
            self.handle_alert() # 조회 후 경고창 뜨면 닫기
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
                print("   ⚠️ 데이터가 없습니다. (옵션 선택 실패 또는 데이터 끝)")
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