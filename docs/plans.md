# Xetical 구현 계획 (아직 만들지 않은 것)

## 1-4. 실행 파일(.exe) 만들기 — 완료(v1.2.0, 포터블 zip)

> 2026-10-21에 만들었다. `scripts/build_exe.py`가 아이콘·PyInstaller(onedir)·쓰지 않는 Qt/구글 API 파일 정리·zip까지 한다.
> 데이터 위치: exe 옆 `portable.txt`가 있으면 `data\`, 없으면 `%APPDATA%\Xetical`(`config.py`). 자동 실행은 exe 경로를 등록한다.
> **남은 것:** Inno Setup 설치 프로그램(이 PC에 Inno Setup이 없어 보류, 설치는 사용자 허락 필요), exe 파일 속성의 버전 정보.

**목표:** Python이 없는 PC에서도 `Xetical.exe` 하나로 실행되고, 작업 관리자에도 "Xetical"로 보이게 한다.

**방법:** PyInstaller로 한 폴더(onedir) 묶음을 만든다.
- 한 파일(onefile)로 묶으면 실행할 때마다 임시 폴더에 풀어서 시작이 느리다.
- 백신이 의심하는 경우도 많아서 onedir을 쓴다.

```
pyinstaller --noconsole --name Xetical --icon assets/xetical.ico ^
  --add-data "README.md;." --collect-data googleapiclient --collect-submodules googleapiclient ^
  --hidden-import korean_lunar_calendar main.pyw
```

**할 일**
1. `assets/xetical.ico` 만들기. 지금 코드로 그리는 달력 아이콘(`widgets.app_icon`)을 16~256px로 저장한다.
2. 저장 위치 바꾸기.
   - 지금 `data/`는 프로그램 폴더 안에 있다. exe를 `Program Files`에 두면 쓰기 권한이 없다.
   - 그래서 `data/`를 `%APPDATA%\Xetical`로 옮길 수 있게 한다.
   - 프로그램 폴더에 `portable.txt`가 있으면 지금처럼 폴더 안에 둔다.
   - 바꿀 곳은 `config.py`의 `DATA_DIR` 한 군데다.
3. `googleapiclient`는 API 설명 파일(discovery JSON)을 패키지 데이터로 읽는다. 그래서 `--collect-data`가 꼭 필요하다.
4. 자동 실행 경로 고치기. 지금 `autostart_command()`는 `pythonw.exe main.pyw`로 등록한다. exe일 때는(`sys.frozen`) exe 경로를 등록하도록 바꾼다.
5. 설치 프로그램은 Inno Setup으로 만든다(선택). 할 일:
   - 시작 메뉴 바로가기
   - 제거 시 `%APPDATA%\Xetical`을 지울지 묻기
   - 기존 `Xetical.bat` 사용자의 `data/` 옮겨 오기
6. 서명은 하지 않는다(개인용). 그래서 처음 실행할 때 SmartScreen 경고가 뜰 수 있다고 README에 적는다.

**확인할 것:** 깨끗한 PC나 Windows 샌드박스에서 아래를 확인한다.
- 실행, 로그인, 동기화
- 할 일
- 단축키
- 아이콘 클릭 통과(마우스 훅)
- 자동 실행

## 2-1. 빠른 입력 — 계획만

**목표:** "내일 3시 치과", "금요일 저녁 7시 회식 2시간", "11/3 월세 매달"처럼 한 줄로 쓰면 날짜·시간·반복을 알아서 채운다.

**어디서 입력하나**
- 달력 우클릭 → "빠른 입력…": 작은 입력창이 뜬다.
- 단축키(예: Ctrl+Alt+N)로 어디서든 입력창을 띄운다.
- 셀 편집기에서 줄 앞에 시각을 쓰면(`15:00 치과`) 시간 일정으로 만든다.

**해석기(`xetical/quickparse.py`)**
- 외부 AI 없이 정규식과 규칙으로 처리한다(오프라인, 무료, 빠름).
- 결과: `{"title", "start", "end", "all_day", "recurrence", "alarm"}` + 해석한 부분을 표시한 문자열(미리보기용)

| 종류 | 예시 |
|---|---|
| 날짜 | 오늘, 내일, 모레, 글피, 다음 주 월요일, 이번 주 금요일, 11/3, 11월 3일, 3일(이번 달, 지났으면 다음 달), 2026-11-03 |
| 시각 | 3시, 오후 3시, 15:30, 3시 반, 저녁 7시(→19시), 아침 8시, 정오, 자정 |
| 길이 | 2시간, 30분, 3시~5시, 3시부터 5시까지 |
| 반복 | 매일, 매주 (요일), 격주, 매달 (n일), 매년, 평일마다 → RRULE |
| 알림 | "10분 전 알림", "하루 전 알림" → alarm 문자열 |
| 할 일 | 끝에 "할 일"·"까지"가 붙으면 구글 할 일로 |

시간이 없으면 종일 일정이고, 해석하고 남은 글자가 제목이 된다.

**화면:** 입력하는 동안 아래에 "11월 3일(월) 15:00–16:00 · 치과 · 매주"처럼 미리보기를 보여 준다. Enter로 저장하고, Tab을 누르면 자세한 일정 창으로 넘어간다.

**시험:** 문장 → 결과 표를 `tests/test_quickparse.py`에 50개 정도 만들어 둔다. 기준일을 고정한 함수로 테스트한다.

**나중에(선택):** 규칙으로 못 푼 문장만 Claude API에 보내 해석하는 옵션. 기본값은 끄고, API 키는 사용자가 넣는다.

## 2-8. 일정 종류별 아이콘 — 계획만

**목표:** 칸에서 일정 앞에 종류 아이콘을 작게 붙여 한눈에 구분한다.

**구분 기준(구글 데이터에서 바로 알 수 있는 것부터)**

| 아이콘 | 조건 | 구글 데이터 |
|---|---|---|
| 🎂 | 생일 | `eventType == "birthday"` |
| 📹 | 화상 회의 | `conferenceData` 또는 `hangoutLink` 있음 |
| 👥 | 참석자가 있는 일정 | `attendees` 2명 이상 |
| 📍 | 장소 있음 | `location` |
| 🏖 | 부재중 | `eventType == "outOfOffice"` |
| 🎯 | 집중 시간 | `eventType == "focusTime"` |
| ✈ | 항공·숙박 등 Gmail 자동 일정 | `source` / `eventType == "fromGmail"` |
| ↻ | 반복 | 이미 있는 `recurring` |

**구현**
1. `gcal._parse`에서 위 값을 읽어 `Event.kind`(문자열 목록)를 채운다. 추가 API 호출은 필요 없다(이미 받는 응답에 있음).
2. `view.item_text` 앞에 아이콘을 붙인다.
   - 이모지는 글꼴에 따라 크기가 들쭉날쭉하다. 그래서 `QPainter`로 직접 그리는 작은 단색 아이콘(퍼즐·구름 버튼처럼)을 쓴다. 이러면 글자색을 따라가서 DesktopCal 느낌과도 맞는다.
3. 설정(셀 탭): "일정 종류 아이콘 표시", 종류별로 켜고 끄기.
4. 사용자가 직접 정하는 아이콘(★ 중요, 💊 약 등)
   - `extendedProperties.private.xeticalIcon`에 저장한다.
   - 일정 창 글자색 옆에 아이콘 고르기 단추를 둔다.
   - 셀 편집기 우클릭에도 "아이콘" 메뉴를 넣는다.

**순서:** 1 → 2(생일·화상회의·장소부터) → 3 → 4
