# ccbot 스킬 메뉴 설계

> 텔레그램 `/` 커맨드 메뉴에 설치된 Claude Code 스킬을 자동 등록하여, 텔레그램에서 바로 스킬을 실행할 수 있게 한다.

## 배경

현재 ccbot은 `/clear`, `/compact` 같은 기본 Claude Code 명령만 텔레그램에서 사용 가능하다. superpowers, pr-review-toolkit 등 설치된 플러그인 스킬은 텔레그램에서 목록 조회나 실행이 불가능하다.

## 핵심 동작

```
ccbot 시작
  → ~/.claude/plugins/cache/ 스캔
  → SKILL.md에서 name, description 파싱
  → bot.set_my_commands()로 텔레그램 커맨드 등록

사용자가 / 입력
  → 기존 봇 명령 + 설치된 스킬 목록 표시
  → 즐겨찾기 상단, 그 다음 사용빈도순

사용자가 /brainstorming 탭
  → forward_command_handler로 전달
  → tmux send-keys "/brainstorming" Enter
  → Claude가 스킬 실행
```

## 모듈 설계

### skill_registry.py (신규)

스킬 스캔, 캐싱, 사용 통계, 즐겨찾기를 전담하는 모듈.

#### 클래스: SkillRegistry

```python
class SkillInfo:
    name: str           # 원본 스킬 이름 (e.g. "brainstorming")
    command: str        # 텔레그램 커맨드 (e.g. "brainstorming")
    description: str    # SKILL.md에서 파싱한 설명
    plugin: str         # 소속 플러그인 (e.g. "superpowers")
    slash_command: str  # Claude에 전달할 원본 (e.g. "/brainstorming")

class SkillRegistry:
    def __init__(self, plugins_dir: str, state_path: str): ...
    def scan(self) -> list[SkillInfo]: ...
    def get_sorted_commands(self, project_dir: str | None) -> list[BotCommand]: ...
    def record_usage(self, command: str, project_dir: str) -> None: ...
    def toggle_favorite(self, command: str) -> bool: ...
    def is_favorite(self, command: str) -> bool: ...
    def get_favorites(self) -> list[str]: ...
```

#### 스캔 대상

```
~/.claude/plugins/cache/
├── claude-plugins-official/
│   ├── superpowers/5.0.7/skills/        → brainstorming, debug, tdd, ...
│   │   └── */SKILL.md                    → name, description 파싱
│   ├── superpowers/5.0.7/commands/       → deprecated, 스캔 제외
│   └── pr-review-toolkit/*/skills/       → code-reviewer, ...
│       └── */SKILL.md
├── claude-community/
├── imgompanda/
└── ...
```

#### SKILL.md 파싱 규칙

```yaml
---
name: brainstorming
description: "You MUST use this before any creative work..."
---
```

- `name` → 커맨드 이름으로 사용
- `description` → 첫 문장만 추출하여 텔레그램 커맨드 설명 (256자 제한)
- 하이픈 → 언더스코어 변환 (`review-pr` → `review_pr`)
- 이름 충돌 시 플러그인 접두사 (`sp_brainstorming`)

### 커맨드 이름 매핑

| 원본 스킬 이름 | 텔레그램 커맨드 | Claude 전달 |
|---------------|---------------|------------|
| `superpowers:brainstorming` | `/brainstorming` | `/brainstorming` |
| `superpowers:systematic-debugging` | `/systematic_debugging` | `/systematic-debugging` |
| `pr-review-toolkit:code-reviewer` | `/pr_code_reviewer` | `/code-reviewer` |
| `superpowers:writing-plans` | `/writing_plans` | `/writing-plans` |

플러그인 접두사 생략이 기본이며, 이름 충돌 시에만 접두사를 붙인다.

### 커맨드 등록 순서

`bot.set_my_commands()`에 전달할 순서:

1. **기존 봇 명령** — `start`, `history`, `screenshot`, `esc`, `unbind`, `usage`
2. **즐겨찾기 스킬** — `skill_state.json`의 favorites 순서
3. **현재 프로젝트 사용빈도순** — 해당 프로젝트 디렉토리에서 많이 쓴 순
4. **나머지** — 알파벳순

### 상태 파일

```json
// ~/.ccbot/skill_state.json
{
  "favorites": ["commit", "brainstorming"],
  "usage": {
    "/Users/pakjungeol/Documents/Insudeal/CeoReport": {
      "commit": 15,
      "review_pr": 8
    },
    "/Users/pakjungeol/Documents/Claude": {
      "brainstorming": 12,
      "writing_plans": 5
    }
  }
}
```

## bot.py 변경

### post_init

```python
async def post_init(app: Application) -> None:
    # ... 기존 초기화 ...
    skill_registry.scan()
    commands = skill_registry.get_sorted_commands(project_dir=None)
    await app.bot.set_my_commands(commands)
```

### forward_command_handler 확장

스킬 커맨드 실행 시 usage 기록:

```python
async def forward_command_handler(update, context):
    # ... 기존 로직 ...
    cmd = cc_slash.lstrip("/")
    if skill_registry.is_skill(cmd):
        project_dir = session_manager.get_project_dir(wid)
        skill_registry.record_usage(cmd, project_dir)
        # 커맨드→원본 스킬명 변환하여 전달
        cc_slash = skill_registry.get_slash_command(cmd)
    # ... tmux send-keys ...
```

### /favorite 명령

```python
async def favorite_command(update, context):
    skills = skill_registry.get_all_skills()
    keyboard = []
    for skill in skills:
        star = "⭐ " if skill_registry.is_favorite(skill.command) else ""
        keyboard.append([InlineKeyboardButton(
            f"{star}{skill.command} — {skill.description[:40]}",
            callback_data=f"fav:{skill.command}"
        )])
    await safe_reply(update.message, "즐겨찾기 토글:", reply_markup=InlineKeyboardMarkup(keyboard))
```

즐겨찾기 변경 시 `bot.set_my_commands()`를 다시 호출하여 순서 갱신.

## 커맨드 메뉴 갱신 타이밍

| 이벤트 | 동작 |
|--------|------|
| ccbot 시작 | 전체 스캔 + 커맨드 등록 |
| 즐겨찾기 토글 | 커맨드 순서 재등록 |
| 세션 바인딩 변경 | 해당 프로젝트 사용빈도 반영하여 재등록 |

## 스코프 외 (향후)

- 매크로/조합 스킬
- 스킬 파라미터 입력 UI
- 프로젝트별 커맨드 메뉴 자동 전환 (토픽 진입 시)
- 커스텀 스킬 생성 (`~/.ccbot/custom_skills/`)
