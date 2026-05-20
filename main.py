import os
import re
import time
from pathlib import Path
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

# 로컬 PC Downloads 폴더에 저장
DOWNLOAD_DIR = Path.home() / "Downloads" / "Nextrise_IR"

LOG_DIR = BASE_DIR / "logs"
DEBUG_DIR = BASE_DIR / "debug"
LOG_PATH = LOG_DIR / "download_log.csv"

# 로컬 PC에서는 False 가능. 창 없이 돌리고 싶으면 True.
HEADLESS = False

# 전체 회사 수 기준 역순 넘버링
DOWNLOAD_NUMBER_BASE = 1045

# 페이지당 회사 수
PAGE_SIZE = 10

# 안전장치: 최대 페이지 수. 1045개 / 10개 = 약 105페이지
MAX_PAGES = 200


def clean_filename(name: str) -> str:
    name = str(name).strip()
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name)
    return name[:150]


def debug_screenshot(page, filename: str, full_page: bool = False):
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / filename
    page.screenshot(path=str(path), full_page=full_page)
    print(f"디버그 이미지 저장: {path}")


def write_log(row: dict):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([row])
    header = not LOG_PATH.exists()
    df.to_csv(LOG_PATH, mode="a", header=header, index=False, encoding="utf-8-sig")


def get_env_value(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ValueError(f".env 파일에 {key} 값을 입력하세요.")
    return value


def get_download_number(page_no: str, item_index_zero_based: int) -> int:
    page_num = int(page_no)
    global_zero_based_index = (page_num - 1) * PAGE_SIZE + item_index_zero_based
    return DOWNLOAD_NUMBER_BASE - global_zero_based_index


def login(page):
    login_url = get_env_value("LOGIN_URL")
    user_id = get_env_value("IR_SITE_ID")
    password = get_env_value("IR_SITE_PASSWORD")

    page.goto(login_url, wait_until="domcontentloaded")

    print("현재 URL:", page.url)
    print("페이지 제목:", page.title())
    debug_screenshot(page, "01_login_page.png")

    email_input = page.get_by_placeholder("Email")
    password_input = page.get_by_placeholder("Password")
    login_button = page.locator("#btn_login")

    email_input.click()
    email_input.press("Control+A")
    email_input.press("Backspace")
    email_input.type(user_id, delay=50)

    password_input.click()
    password_input.press("Control+A")
    password_input.press("Backspace")
    password_input.type(password, delay=50)

    page.keyboard.press("Tab")

    page.evaluate("""
    () => {
        const email = document.querySelector('input[placeholder="Email"]');
        const password = document.querySelector('input[placeholder="Password"]');

        for (const el of [email, password]) {
            if (!el) continue;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
        }
    }
    """)

    page.wait_for_timeout(1000)

    print("이메일 입력값:", email_input.input_value())
    print("비밀번호 입력 여부:", bool(password_input.input_value()))
    print("로그인 버튼 disabled:", login_button.get_attribute("disabled"))

    page.wait_for_function(
        """() => {
            const btn = document.querySelector('#btn_login');
            return btn && !btn.disabled;
        }""",
        timeout=10000
    )

    login_button.click()

    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)

    print("로그인 후 URL:", page.url)
    debug_screenshot(page, "03_after_login.png")


def close_main_popup_if_exists(page):
    print("메인 팝업 확인 중...")

    page.wait_for_timeout(1000)

    if page.locator(".main-pop").count() == 0:
        print(".main-pop 없음")
        return

    visible_popup_count = page.locator(".main-pop .nextrise_popup:visible").count()

    if visible_popup_count == 0:
        print("보이는 팝업 없음")
        return

    print(f"보이는 팝업 개수: {visible_popup_count}")
    debug_screenshot(page, "04_before_popup_close.png")

    try:
        today_button = page.locator(".main-pop .nextrise_popup:visible .today").first
        if today_button.count() > 0:
            print("'오늘 하루 보지 않기' 클릭")
            today_button.click(timeout=5000)
            page.wait_for_timeout(1000)
            debug_screenshot(page, "05_after_today_click.png")
    except Exception as e:
        print("'오늘 하루 보지 않기' 클릭 실패:", e)

    try:
        if page.locator(".main-pop .nextrise_popup:visible").count() > 0:
            close_button = page.locator(".main-pop .nextrise_popup:visible .close").first
            if close_button.count() > 0:
                print("팝업 X 버튼 클릭")
                close_button.click(timeout=5000)
                page.wait_for_timeout(1000)
                debug_screenshot(page, "06_after_popup_close_click.png")
    except Exception as e:
        print("팝업 X 버튼 클릭 실패:", e)

    try:
        if page.locator(".main-pop .nextrise_popup:visible").count() > 0:
            print("팝업이 계속 보여서 클릭 방해만 비활성화")
            page.evaluate("""
            () => {
                const mainPop = document.querySelector('.main-pop');
                if (mainPop) {
                    mainPop.style.pointerEvents = 'none';
                    mainPop.style.opacity = '0';
                    mainPop.style.visibility = 'hidden';
                }
            }
            """)
            page.wait_for_timeout(500)
            debug_screenshot(page, "07_after_popup_neutralize.png")
        else:
            print("팝업 닫힘 확인")
    except Exception as e:
        print("팝업 비활성화 실패:", e)


def click_meetup_button(page):
    print("우측 MEETUP 버튼 클릭 시도")
    debug_screenshot(page, "08_before_meetup_button_click.png")

    meetup_button = page.locator("#meetupBtn > .i_btn")

    if meetup_button.count() == 0:
        debug_screenshot(page, "08_meetup_button_not_found.png")
        raise RuntimeError("#meetupBtn > .i_btn 버튼을 찾지 못했습니다.")

    meetup_button.click(timeout=10000)
    page.wait_for_timeout(1000)

    is_open = page.locator("#meetupBtn.open").count() > 0
    print("MEETUP 메뉴 open 상태:", is_open)

    if not is_open:
        print("MEETUP 메뉴가 open 상태가 아니므로 JS로 open class 추가")
        page.evaluate("""
        () => {
            const menu = document.querySelector('#meetupBtn');
            if (menu) menu.classList.add('open');
        }
        """)
        page.wait_for_timeout(500)

    debug_screenshot(page, "09_after_meetup_button_click.png")


def click_meetup_company_application(page, context):
    print("'밋업 기업신청' 메뉴 클릭 시도")
    debug_screenshot(page, "10_before_company_application_click.png")

    page.on("dialog", lambda dialog: (
        print(f"Dialog 발생: {dialog.message}"),
        dialog.accept()
    ))

    page.evaluate("""
    () => {
        const menu = document.querySelector('#meetupBtn');
        if (menu) menu.classList.add('open');
    }
    """)
    page.wait_for_timeout(1000)

    try:
        print("o2meetCommon.fnGoO2meet 로딩 대기")
        page.wait_for_function(
            """
            () => {
                return (
                    typeof window.o2meetCommon === 'object' &&
                    typeof window.o2meetCommon.fnGoO2meet === 'function'
                );
            }
            """,
            timeout=15000
        )
        print("o2meetCommon.fnGoO2meet 로딩 확인")
    except Exception as e:
        print("o2meetCommon.fnGoO2meet 로딩 확인 실패:", e)
        debug_screenshot(page, "10_o2meet_common_not_ready.png")

    target = page.locator("#meetupBtn button[onclick*='navigateToMeetUpMenu(3)']")
    print("밋업 기업신청 버튼 개수:", target.count())

    if target.count() == 0:
        debug_screenshot(page, "10_company_button_not_found.png")
        raise RuntimeError("밋업 기업신청 버튼을 찾지 못했습니다.")

    before_url = page.url
    before_page_count = len(context.pages)

    try:
        print("밋업 기업신청 버튼 클릭")
        target.first.click(timeout=10000)
    except Exception as e:
        print("버튼 클릭 실패, JS 함수 직접 호출로 재시도:", e)

        page.evaluate("""
        () => {
            if (typeof navigateToMeetUpMenu === 'function') {
                navigateToMeetUpMenu(3);
            } else {
                throw new Error('navigateToMeetUpMenu 함수가 없습니다.');
            }
        }
        """)

    print("밋업 기업신청 클릭 후 5초 대기")
    page.wait_for_timeout(5000)

    after_page_count = len(context.pages)

    if after_page_count > before_page_count:
        print("새 페이지/탭 감지")
        new_page = context.pages[-1]
        new_page.wait_for_load_state("domcontentloaded", timeout=20000)
        new_page.wait_for_timeout(3000)

        print("새 페이지 URL:", new_page.url)
        debug_screenshot(new_page, "11_new_page_after_company_application.png")
        return new_page

    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass

    print("클릭 전 URL:", before_url)
    print("클릭 후 URL:", page.url)
    debug_screenshot(page, "11_after_company_application_click.png")

    if page.url == before_url:
        print("URL 변화 없음. navigateToMeetUpMenu(3) 직접 호출 재시도")

        try:
            before_second_page_count = len(context.pages)

            page.evaluate("""
            () => {
                if (typeof navigateToMeetUpMenu === 'function') {
                    navigateToMeetUpMenu(3);
                    return true;
                }
                return false;
            }
            """)

            print("직접 호출 후 5초 대기")
            page.wait_for_timeout(5000)

            if len(context.pages) > before_second_page_count:
                print("직접 호출 후 새 페이지/탭 감지")
                new_page = context.pages[-1]
                new_page.wait_for_load_state("domcontentloaded", timeout=20000)
                new_page.wait_for_timeout(3000)

                print("새 페이지 URL:", new_page.url)
                debug_screenshot(new_page, "11_new_page_after_direct_function_call.png")
                return new_page

            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

            print("직접 호출 후 URL:", page.url)
            debug_screenshot(page, "11_after_direct_function_call.png")

        except Exception as e:
            print("navigateToMeetUpMenu(3) 직접 호출 실패:", e)
            debug_screenshot(page, "11_direct_function_call_failed.png")

    return page


def go_to_meetup_company_application_page(page, context):
    print("밋업 기업신청 페이지 이동 시작")

    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1000)

    close_main_popup_if_exists(page)
    click_meetup_button(page)

    active_page = click_meetup_company_application(page, context)

    return active_page


def click_search_button_on_company_page(page):
    print("'검색하기' 버튼 클릭 준비")
    debug_screenshot(page, "12_before_search_click.png")

    try:
        print("fnCompanyList 함수 로딩 대기")
        page.wait_for_function(
            """
            () => typeof window.fnCompanyList === 'function'
            """,
            timeout=15000
        )
        print("fnCompanyList 함수 로딩 확인")
    except Exception as e:
        print("fnCompanyList 함수 로딩 확인 실패:", e)
        debug_screenshot(page, "12_fn_company_list_not_ready.png")

    try:
        search_button = page.locator("button[onclick*='fnCompanyList']").filter(has_text="검색하기")

        print("검색하기 버튼 개수:", search_button.count())

        if search_button.count() > 0:
            print("'검색하기' 버튼 클릭")
            search_button.first.click(timeout=10000)
        else:
            print("'검색하기' 버튼 selector 미발견, 텍스트 버튼으로 재시도")
            page.get_by_role("button", name="검색하기").click(timeout=10000)

    except Exception as e:
        print("'검색하기' 버튼 클릭 실패, fnCompanyList(1) 직접 호출로 재시도:", e)

        page.evaluate("""
        () => {
            if (typeof window.fnCompanyList === 'function') {
                window.fnCompanyList(1);
            } else {
                throw new Error('fnCompanyList 함수가 없습니다.');
            }
        }
        """)

    print("'검색하기' 클릭 후 5초 대기")
    page.wait_for_timeout(5000)

    wait_for_search_results(page)

    debug_screenshot(page, "14_after_search_click.png")


def wait_for_search_results(page):
    try:
        print("검색 결과 또는 IR자료 다운로드 버튼 생성 대기")
        page.wait_for_function(
            """
            () => {
                const irButtons = Array.from(document.querySelectorAll('button'))
                    .filter(btn => (btn.getAttribute('onclick') || '').includes('companyIRFileDownload')).length;

                const rows1 = document.querySelectorAll('#search_list tbody tr').length;
                const rows2 = document.querySelectorAll('#search_list_wait tbody tr').length;

                const table1 = document.querySelector('#search_list');
                const table2 = document.querySelector('#search_list_wait');

                const table1Visible = !!table1 && window.getComputedStyle(table1).display !== 'none';
                const table2Visible = !!table2 && window.getComputedStyle(table2).display !== 'none';

                return irButtons > 0 || rows1 > 0 || rows2 > 0 || table1Visible || table2Visible;
            }
            """,
            timeout=30000
        )
        print("검색 결과 또는 IR자료 다운로드 버튼 생성 확인")

    except Exception as e:
        print("검색 결과 대기 실패:", e)
        debug_screenshot(page, "13_search_result_wait_failed.png")

    ir_button_count = page.locator("button[onclick*='companyIRFileDownload']").count()
    print("IR자료 다운로드 버튼 개수:", ir_button_count)


def get_ir_download_buttons(page):
    return page.locator(
        "button[onclick*='companyIRFileDownload'], "
        "a:has-text('다운로드'), "
        "button:has-text('다운로드'), "
        "a:has-text('Download'), "
        "button:has-text('Download'), "
        "a:has-text('IR'), "
        "button:has-text('IR'), "
        "a:has-text('자료'), "
        "button:has-text('자료'), "
        "a:has-text('첨부'), "
        "button:has-text('첨부')"
    )


def download_files_on_current_page(page, page_no: str):
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    debug_screenshot(page, f"15_before_download_scan_page_{page_no}.png")

    download_buttons = get_ir_download_buttons(page)
    button_count = download_buttons.count()

    print(f"[page {page_no}] 다운로드 후보 버튼 {button_count}개 발견")

    if button_count == 0:
        debug_screenshot(page, f"16_no_download_buttons_page_{page_no}.png")

        write_log({
            "timestamp": datetime.now().isoformat(),
            "page_no": page_no,
            "item_index": "",
            "download_number": "",
            "status": "no_buttons",
            "file_path": "",
            "error": "다운로드 버튼 없음"
        })

        print(f"[page {page_no}] 다운로드 버튼을 찾지 못했습니다.")
        return

    for i in range(button_count):
        download_number = get_download_number(page_no, i)

        # 번호가 0 이하로 내려가면 중단
        if download_number <= 0:
            print(f"다운로드 번호가 0 이하입니다. 중단합니다. download_number={download_number}")
            return

        try:
            download_buttons = get_ir_download_buttons(page)
            button = download_buttons.nth(i)

            print(
                f"[page {page_no}] "
                f"{i + 1}/{button_count}번째 다운로드 시도 "
                f"-> 파일 번호 {download_number}"
            )

            with page.expect_download(timeout=60000) as download_info:
                button.click()

            download = download_info.value
            suggested_name = download.suggested_filename
            safe_name = clean_filename(suggested_name)

            save_path = DOWNLOAD_DIR / f"{download_number}_{safe_name}"

            if save_path.exists():
                stem = save_path.stem
                suffix = save_path.suffix
                save_path = DOWNLOAD_DIR / f"{stem}_{int(time.time())}{suffix}"

            download.save_as(str(save_path))

            write_log({
                "timestamp": datetime.now().isoformat(),
                "page_no": page_no,
                "item_index": i + 1,
                "download_number": download_number,
                "status": "success",
                "file_path": str(save_path),
                "error": ""
            })

            print(f"저장 완료: {save_path.name}")
            time.sleep(0.8)

        except PlaywrightTimeoutError as e:
            write_log({
                "timestamp": datetime.now().isoformat(),
                "page_no": page_no,
                "item_index": i + 1,
                "download_number": download_number,
                "status": "failed",
                "file_path": "",
                "error": f"timeout: {str(e)}"
            })

            debug_screenshot(page, f"17_download_timeout_page_{page_no}_{i + 1}.png")
            print(f"[page {page_no}] 실패: {i + 1}번째 버튼 timeout")

        except Exception as e:
            write_log({
                "timestamp": datetime.now().isoformat(),
                "page_no": page_no,
                "item_index": i + 1,
                "download_number": download_number,
                "status": "failed",
                "file_path": "",
                "error": str(e)
            })

            debug_screenshot(page, f"18_download_error_page_{page_no}_{i + 1}.png")
            print(f"[page {page_no}] 실패: {i + 1}번째 버튼 {e}")


def get_first_company_row_id(page):
    try:
        row_id = page.evaluate("""
        () => {
            const row = document.querySelector(
                '#search_list_wait tbody tr[id^="company_view_"], #search_list tbody tr[id^="company_view_"]'
            );
            return row ? row.id : '';
        }
        """)
        return row_id
    except Exception:
        return ""


def get_current_active_page(page) -> str:
    try:
        current_active_page = page.evaluate("""
        () => {
            const active = document.querySelector('#paging li.active, .pagination li.active');
            return active ? active.innerText.trim() : '';
        }
        """)
        return current_active_page
    except Exception:
        return ""


def wait_until_page_changed(page, before_first_company_id: str, target_page_no: int):
    try:
        page.wait_for_function(
            """
            ([beforeId, targetPageNo]) => {
                const row = document.querySelector(
                    '#search_list_wait tbody tr[id^="company_view_"], #search_list tbody tr[id^="company_view_"]'
                );

                const active = document.querySelector('#paging li.active, .pagination li.active');
                const activeText = active ? active.innerText.trim() : '';

                if (!row) return false;

                if (String(activeText) === String(targetPageNo)) {
                    return true;
                }

                if (!beforeId) return true;

                return row.id !== beforeId;
            }
            """,
            arg=[before_first_company_id, target_page_no],
            timeout=30000
        )
        print("페이지 변경 확인")
    except Exception as e:
        print("페이지 변경 확인 실패:", e)

    page.wait_for_timeout(1000)

    current_active_page = get_current_active_page(page)
    print("현재 active 페이지:", current_active_page)


def go_to_next_page(page, current_page_no: int) -> bool:
    """
    다음 페이지로 이동합니다.

    우선 현재 페이지 + 1 숫자 버튼을 클릭합니다.
    숫자 버튼이 없으면 오른쪽 화살표를 클릭합니다.
    더 이상 다음 페이지가 없으면 False를 반환합니다.
    """
    next_page_no = current_page_no + 1

    print(f"{next_page_no}페이지 이동 시도")
    debug_screenshot(page, f"19_before_go_to_page_{next_page_no}.png")

    before_first_company_id = get_first_company_row_id(page)
    print("이동 전 첫 row id:", before_first_company_id)

    # 1순위: 다음 페이지 숫자 버튼 클릭
    try:
        next_number_button = page.locator("#paging li").filter(has_text=str(next_page_no)).first

        if next_number_button.count() > 0:
            text = next_number_button.inner_text().strip()
            if text == str(next_page_no):
                print(f"페이지 번호 {next_page_no} 클릭")
                next_number_button.click(timeout=10000)
                page.wait_for_timeout(5000)

                wait_until_page_changed(page, before_first_company_id, next_page_no)
                debug_screenshot(page, f"20_after_go_to_page_{next_page_no}_by_number.png")

                active_page = get_current_active_page(page)
                if active_page == str(next_page_no):
                    print(f"{next_page_no}페이지 이동 완료")
                    return True

                print(f"숫자 클릭 후 active 페이지가 예상과 다름: {active_page}")

    except Exception as e:
        print(f"페이지 번호 {next_page_no} 클릭 실패:", e)

    # 2순위: 오른쪽 화살표 클릭
    try:
        print("오른쪽 화살표 클릭 시도")

        # onclick이 없는 next는 마지막 페이지의 비활성 버튼일 수 있으므로 onclick 있는 것 우선
        next_button = page.locator(
            "#paging a.next[onclick], "
            "#paging li a.next[onclick], "
            ".pagination a.next[onclick], "
            ".pagination li a.next[onclick]"
        ).first

        if next_button.count() == 0:
            print("onclick 있는 오른쪽 화살표 없음. 더 이상 다음 페이지가 없다고 판단")
            return False

        next_button.click(timeout=10000)
        page.wait_for_timeout(5000)

        wait_until_page_changed(page, before_first_company_id, next_page_no)
        debug_screenshot(page, f"20_after_go_to_page_{next_page_no}_by_arrow.png")

        active_page = get_current_active_page(page)
        print("오른쪽 화살표 클릭 후 active 페이지:", active_page)

        if active_page == str(next_page_no) or active_page != str(current_page_no):
            print(f"{next_page_no}페이지 이동 완료")
            return True

        print("오른쪽 화살표 클릭 후에도 페이지 변화 없음")
        return False

    except Exception as e:
        debug_screenshot(page, f"20_go_to_page_{next_page_no}_failed.png")
        print(f"{next_page_no}페이지 이동 실패:", e)
        return False


def download_all_pages(page):
    """
    1페이지부터 마지막 페이지까지 순차적으로 다운로드합니다.
    """
    current_page_no = 1

    while current_page_no <= MAX_PAGES:
        print("=" * 80)
        print(f"{current_page_no}페이지 다운로드 시작")
        print("=" * 80)

        download_files_on_current_page(page, page_no=str(current_page_no))

        print(f"{current_page_no}페이지 다운로드 종료")

        moved = go_to_next_page(page, current_page_no)

        if not moved:
            print("더 이상 다음 페이지가 없어 전체 다운로드를 종료합니다.")
            break

        current_page_no += 1

    if current_page_no > MAX_PAGES:
        print(f"MAX_PAGES={MAX_PAGES}에 도달하여 종료합니다.")


def main():
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    print(f"다운로드 저장 폴더: {DOWNLOAD_DIR}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS
        )

        context = browser.new_context(
            accept_downloads=True
        )

        page = context.new_page()

        login(page)

        active_page = go_to_meetup_company_application_page(page, context)

        click_search_button_on_company_page(active_page)

        print("전체 페이지 다운로드 시작")
        download_all_pages(active_page)
        print("전체 페이지 다운로드 종료")

        context.close()
        browser.close()


if __name__ == "__main__":
    main()