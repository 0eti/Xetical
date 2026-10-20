# Xetical 인수인계 (2026-10-20)

다음 세션은 이 문서부터 읽고 이어서 작업한다. **사용자에게는 항상 한국어로 답한다.**

> **이 문서는 공개 저장소에 올라간다.** 메일 주소, 캘린더·할 일 이름, Cloud 프로젝트 번호, 사용자 PC에 깔린 프로그램 같은 개인정보는 적지 않는다.

---

## 1. 무엇인가

- **Xetical** (c는 소문자): DesktopCal(중국산 바탕화면 달력)을 본뜬 **구글 캘린더·구글 할 일 바탕화면 달력**. Windows 전용, Python 3.13 + PySide6.
- **위치:** `D:\Dev\Projects\XetiCal`. 폴더 이름은 원래 GoogleCal이었다.
- **git:** 2026-10-21부터 git으로 관리한다. 공개 GitHub 저장소 https://github.com/0eti/Xetical (`main`).
  - 커밋 작성자는 저장소 로컬 설정(0eti, GitHub noreply 메일). `data/`, `.venv/`, `client_secret*.json`은 `.gitignore`로 뺐다.
  - 파일마다 줄끝이 다르다(CRLF/LF 섞임, 파일 하나 안에서는 같음). `.gitattributes`에서 `*.bat`만 CRLF로 고정.
- **실행:** `Xetical.bat` (CRLF, ASCII 전용). 처음이면 `.venv`를 만들고 `requirements.txt`를 설치한 뒤 `pythonw main.pyw`를 실행한다.
- **사용자가 원하는 방향**
  - **DesktopCal과 최대한 비슷하게.** 특히 설정 창 구성.
  - 기능을 넣을 때마다 목록 → 번호 선택 → 구현 순서로 진행해 왔다.
- **DesktopCal은 아직 설치돼 있고 자동 실행도 등록돼 있다.**
  - 위치: `%APPDATA%\DesktopCal`, DB: `Db\calendar.db`
  - 사용자는 예전 DesktopCal 데이터는 필요 없다고 했다(불러오기 안 함).

## 2. 구조 (`xetical/`)

| 파일 | 역할 |
|---|---|
| `app.py` | 본체. 창·트레이·메뉴(우클릭·☰, 2026-10-20 정리), 동기화, 보낼 편지함, 되돌리기, 할 일·반복 할 일, 복사·붙여넣기, 테마, 설정 백업, 화면 변경 대응, `main()` |
| `view.py` | 바탕화면 달력 창 그리기와 마우스 처리. 4가지 표시 방식, 여러 날 막대, 끌어서 옮기기, ☐ 체크, 선택, 직접 판정하는 더블클릭(`_is_second_click`), 단축키(QShortcut) |
| `desktop.py` | Win32: 창의 owner를 바탕화면 FolderView로 지정, NOACTIVATE, Z순서 잠금(WM_WINDOWPOSCHANGING), 유리 효과, 클릭 통과(WS_EX_TRANSPARENT), `force_foreground` |
| `mousehook.py` | 아이콘 클릭 통과: WH_MOUSE_LL 훅 + 탐색기 리스트뷰에 LVM_HITTEST(VirtualAllocEx로 잡은 메모리)로 아이콘 유무를 묻는다. 빈 곳 클릭은 삼켜서 달력에 넘긴다. |
| `gcal.py` | Google Calendar/Tasks API. extendedProperties.private에 `xeticalDone/Color/Order/Line/Alarm/Countdown/CD` 저장. 반복 범위(one/following/all), 묶음 저장(id 반환), move/respond, 할 일 CRUD, 공휴일, 검색 |
| `jobs.py` | **보낼 편지함(Outbox)**: 모든 쓰기를 `data/outbox.json`에 줄 세움. 오프라인이면 보관했다가 재시도. 임시 id(`tmp-…`)→진짜 id 매핑(`id_map`). 404/410은 성공으로 친다. |
| `editor.py` | DesktopCal식 칸 편집기(한 줄 = 종일 일정 하나). 반복 일정 줄 편집, 범위 묻기, 포커스를 잃으면 자동 저장 |
| `dialogs.py` | 일정 창(반복·알림·카운트다운·초대 응답·기본 알림), 할 일 창(반복 주기 = Xetical 표시용), 모바일 QR, 날짜 선택, `ask_scope` |
| `dayview.py` | 하루 상세 보기(타임라인) |
| `settings.py` | 설정 창. 탭: 캘린더/셀/글꼴 및 색상/고급/할 일·알림/계정/정보. 바꾸면 즉시 적용 |
| `reminders.py` | 알림 팝업, 아침 요약 |
| `cards.py` | 카운트다운 바탕화면 카드 |
| `tools.py` | 검색, 내보내기(txt/CSV), 인쇄 (DesktopCal 불러오기는 지움) |
| `themes.py`, `almanac.py`, `hotkeys.py`, `widgets.py`, `config.py`, `model.py` | 테마 7종, 음력·24절기·주 번호, 전역 단축키, 공용 위젯(OnOff·ColorButton·`confirm`·`app_icon`·`is_dark`), 설정 기본값, Event/EventStore |

**데이터:** `data/` 폴더(포터블). 백업에서 빼는 항목: `config.json`, `token.json`, `client_secret.json`, `cache.json`, `cells.json`, `outbox.json`, `fired.json`, `xetical.log`(회전 1MB×3)

**문서:** `README.md`(사용법), `docs/desktopcal-analysis.md`(DesktopCal 분석), `docs/plans.md`(1-4·2-1·2-8 계획), 이 문서

## 3. 구글 연동 상태

- 사용자 계정의 Google Cloud 프로젝트를 쓴다. OAuth 클라이언트는 데스크톱 앱 유형.
- Calendar API와 Tasks API 모두 켜져 있다.
- 동의 화면 앱 이름: Xetical
- 게시를 위해 홈페이지·개인정보처리방침 페이지를 `site/`에 만들었다.
  - GitHub Pages 계정 0eti(`0eti.github.io`)를 쓰자고 제안했다.
  - **사용자가 실제로 게시했는지는 확인하지 않았다.** 테스트 상태면 토큰이 7일마다 만료된다.
- 권한(scope): `calendar.readonly`, `calendar.events`, `tasks`. 사용자 토큰에는 tasks 권한도 있다.
- 사용자 캘린더는 기본 캘린더 외에 여러 개(숨긴 것 포함), 할 일 목록 1개. 매주 반복하는 할 일이 `task_repeats`에 등록돼 있다.

## 4. 기능 현황

- **완성:** DesktopCal식 화면과 설정 전체, 칸 편집기, 반복·알림·카운트다운, 공휴일·음력·절기, 할 일(마감일 있는 것만, 사용자 선택 "(가)").
- **1차 기능 추가 완료(사용자 번호 기준)**
  - 1-1 칸 ☐ 체크, 1-2 오프라인 대기, 1-3 Ctrl+Z(달력을 클릭해 활성화된 상태에서만), 1-5 아이콘 클릭 통과, 1-6 막대, 1-7 끌어서 옮기기, 1-8 반복 줄 편집, 1-9 모니터 변경, 1-11 초대 응답, 1-12 로그 제한
  - 2-2 하루 상세, 2-3 아침 요약, 2-4 복사·붙여넣기, 2-6 테마
  - 3-1·3-2·3-3·3-4·3-6·3-7·3-8, 4-1·4-2·4-3·4-5·4-6·4-7
- **계획만:** 1-4 exe(**마지막에** 하기로 함), 2-1 빠른 입력, 2-8 종류별 아이콘 → `docs/plans.md`
- **보류·제외**
  - 2-5 스티커 메모: 사용자가 **별도 프로그램으로** 만들 예정
  - 1-10 제외, 2-7·2-9·2-10·2-11·3-5·3-9·3-10 미선택
  - 4-4 위치 잠금은 이미 사실상 있음(조정 모드에서만 움직임)
- **반복 할 일 규칙(중요):** 구글 Tasks API로 완료하면 **구글이 다음 회차를 만들지 않는다**. 그래서 Xetical이 처리한다.
  - 지금 회차를 체크하면 다음 회차를 진짜 할 일로 만든다(중복 확인 포함).
  - 흐린 "예정"을 누르면 그 날짜 회차를 완료 상태로 만든다.
  - 예정은 가장 최근 회차(완료 여부와 상관없이) 뒤로 그린다.
- **메뉴 정리(2026-10-20, 사용자 요청):** 우클릭 메뉴가 너무 길어서 줄였다.
  - 칸 우클릭: 그날 항목 6개 + 실행 취소 + 설정…(☰ 메뉴를 더 이상 붙이지 않음)
  - 일정 우클릭: 일정 항목 + '이 날짜' 하위 메뉴
  - ☰: 날짜로 이동…(월분 선택·날짜 선택 합침), 실행 취소, 내용 검색, 예약된 작업 숨기기, 캘린더 숨기기, 위치 및 크기, 내보내기, 프린트, 설정…, 종료
  - 뺀 것: 오늘로·이전/다음 페이지·동기화·계정(제목줄 버튼), 최대 행·자동 동기화·사용 안내·제품정보(설정 창)
  - 동기화 정보(마지막 동기화·대기 수)는 ☁ 풍선 도움말로 옮겼다(`app.sync_info` → `view.sync_info`).
  - **DesktopCal 불러오기 기능은 통째로 지웠다**(코드·시험·README).
  - 실제 화면 우클릭 확인은 못 했다(게임 창이 앞에 있었음). 시험(`t_more`)으로만 확인.
- **사용자 요청으로 끈 것:** 칸 클릭 점선 강조, 칸 hover 강조(제목줄 버튼 hover와 스크롤바는 남김)

## 5. 시험

`tests/`에 있는 스크립트들(scratch에서 옮겨 옴). 모두 `python tests/X.py <임시폴더>`로 실행한다. 데이터 파일 경로는 임시 폴더로 바꿔 쓴다.

```
set PYTHONIOENCODING=utf-8
set QT_QPA_PLATFORM=offscreen
set QT_QPA_FONTDIR=C:/Windows/Fonts
.venv\Scripts\python.exe -u tests\t_all.py %TEMP%\xt
```

| 스크립트 | 내용 |
|---|---|
| `t_all.py` (32) | 가짜 구글로 핵심 흐름 |
| `t_more.py` (80) | 나머지 기능 전부 |
| `t_repeat.py` (11) | 반복 할 일 |
| `t_fixes.py` (32) | 2026-10-21 리뷰에서 고친 A1~A6·B7~B12 (가짜 구글 API로 `gcal.py` 안쪽까지) |
| `t_google.py` (30) | **실제 구글**: 2027년 1월에 "[Xetical 테스트]" 항목을 만들었다 지운다. 사용자 허락을 받고 돌릴 것 |

**마지막 결과:** 전부 통과(2026-10-21, `t_google` 30/30 포함). 실제 화면 시험도 대부분 통과(아래 6 참고).

## 6. 함정과 주의 (꼭 읽기)

**파일 편집**
- `.bat`는 **CRLF + ASCII만** 쓴다. `sed -i`가 LF로 바꿔서 깨진 적이 있다.
- 파이썬 heredoc 편집에서 줄 끝 `\`(줄 이음)가 들어간 문자열은 매칭이 안 됐다. 이때는 Edit 도구를 쓰거나 스크립트를 파일로 저장해 실행한다.
- `Path.write_text`는 Windows에서 CRLF로 저장한다. 파일 줄끝이 섞여 있으니 문자열을 매칭할 때 주의한다.

**코드**
- **QDialog 속성 이름으로 `done`, `scroll` 등 Qt 메서드 이름을 쓰지 말 것.** `self.done = QCheckBox`가 닫기 기능을 덮어써서 창이 안 닫혔다. 지금은 `done_box`로 바꿨다.
- 대화상자는 **부모 없는 최상위 창**이어야 한다. 달력의 자식이 되면 바탕화면 아래에 깔린다.

**실행과 시험 환경**
- Claude 앱은 MSIX 패키지라, 여기서 띄운 자식 프로세스는 HKCU 레지스트리와 AppData 쓰기가 가상화된다. 그래서 **사용자처럼 실행하려면 `explorer.exe "D:\…\Xetical.bat"`로 띄운다.**
  - 자동 실행 설정도 사용자가 직접 실행한 앱에서 바꿔야 실제로 반영된다.
- 화면 시험(computer-use)에서 주의할 것
  - `pythonw.exe`는 허용 목록에 없다. 시험할 때는 `.venv\Scripts\python.exe main.pyw`를 `-WindowStyle Hidden`으로 띄운다("Python 3.13 (64-bit)" 허용). 끝나면 explorer로 bat를 다시 실행한다.
  - **`type` 도구는 클립보드로 붙여넣는다.** clipboardWrite 권한이 없을 때 **사용자 클립보드 내용이 일정으로 저장되는 사고**가 있었다(지웠다). 반드시 `write_clipboard` 다음에 `ctrl+v`를 쓰고, 저장 전에 확대해서 확인한다.
  - 클립보드에 링크가 들어가면 사용자 PC의 다운로드 관리자가 반응할 수 있다. 미리 끄게 한다.
  - Claude 창이 바탕화면을 덮고 있어서 두 번째 클릭이 그쪽으로 갔다. 시험 전에 Xetical을 활성화하고 **Win+D**를 누르면 해결된다.
  - 클릭 통과 모드에서는 도구 규칙상 탐색기 위 우클릭과 드래그를 할 수 없다. 우회하지 말 것.
- 실제 구글 시험은 정리를 반드시 `finally`에서 한다. 404/410은 무시한다.
- `tests/t_task.py`, `t_virtual.py`는 예전 임시 스크립트라 지금 코드와 맞지 않는다(`t_repeat`이 대신함).

**반복 규칙 값 약속(2026-10-21):** 편집 값 `v["recurrence"]`는 `None`=규칙 그대로, `""`=반복 안 함, `"RRULE:…"`=새 규칙.
`Event.values()`는 `None`을 준다. 일정 창만 `""`나 규칙을 넣는다.

**새 일정 id:** 보낼 편지함이 `create_event`·`apply_ops`의 새 일정에 id(uuid hex)를 미리 붙이고 임시 id와 바로 짝짓는다.
다시 보내도 409로 막혀 두 번 만들어지지 않는다. 할 일(Tasks)은 id를 정할 수 없어 해당 없음.

## 7. 남은 일 / 아이디어

- [ ] **2026-10-21 리뷰의 C(작은 것)는 아직 안 고쳤다.** A·B는 모두 고침(`t_fixes`).
  - 칸 스크롤 끝없이 내려감, ☐ 클릭 영역이 가려진 부분까지, 하루 상세 보기가 로컬 변경 뒤 갱신 안 됨
  - 되돌리기: 삭제 되돌린 뒤 이전 단계 무효, 단일→반복 되돌리기, 할 일 이름 되돌리기 시 반복 규칙
  - 알림: 15분 넘게 놓친 알림, 구글 기본 알림만 있는 일정, 잘못된 알림 값 하나가 전체를 멈춤
  - 탐색기 재시작 뒤 카드 다시 붙이기, 23시 새 일정, 기본 알림 켜면 카운트다운 체크 안 됨, 반복 시작 요일 바꾸기
  - 내보내기 실패 안내, 유리 효과 토글 두 곳, 단축키 오류 문구, 로그아웃 때 편지함, token.json 손상, 매년 반복 할 일 중복

- [ ] 2-1 빠른 입력, 2-8 종류별 아이콘 구현(계획은 `docs/plans.md`)
- [ ] **1-4 exe 묶기는 마지막에**(사용자 지시)
- [ ] 사용자가 앱 게시(7일 토큰 만료 해제)를 했는지 확인
- [ ] 실제 화면에서 못 해 본 것
  - 아이콘 위 클릭 통과(달력 아래에 아이콘이 있는 상황)
  - 클릭 통과 모드에서 우클릭과 드래그
  - 실제 초대 응답
  - 알림 팝업과 카드 드래그
  - 인쇄
- [ ] 마감일 없는 할 일 표시 방식("나")은 사용자가 선택하지 않았다(지금은 "가": 마감일 있는 것만).
