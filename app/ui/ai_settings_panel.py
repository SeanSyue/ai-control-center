from __future__ import annotations

from app.i18n import t

from pathlib import Path
import subprocess

from app.services import ui_preferences
from app.adapters.no_console import NO_WINDOW

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.services.run_summary import summary_line
from app.services import run_settings_options as run_opts
from app.services.ai_settings import AIExecutionSettings, AISettingsStore
from app.core import prompt_markers


class AISettingsPanel(QWidget):
    settings_changed = Signal(object)

    def __init__(self, project_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.project_path = project_path.expanduser().resolve()
        self.store = AISettingsStore(self.project_path)
        self._loading = False
        self._busy = False
        # **打錯字修掉（2026-08-24）。**在這之前這裡是 "AIControlCenter"
        # （沒有空格），而 app 裡其他每一處都是 "AI Control Center"。
        # 在 Windows 登錄檔裡那是**兩個不同的位置**，所以這個面板的展開
        # 狀態跟其他所有偏好各存各的。
        #
        # 功能上只影響一個布林值（他要重新展開一次），但它是老闆問
        # 「會不會亂跑亂發散亂存」的實例：**沒有人規定家在哪的時候，
        # 第二個家是打錯字長出來的，而且沒有任何東西會講。**
        # 現在有 `test_every_setting_lives_in_one_home.py` 會講。
        self._settings = ui_preferences.store()
        self._expanded = self._settings.value(
            "ai_settings_panel/expanded",
            False,
            type=bool,
        )
        self._build_ui()
        self.set_project(self.project_path)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        settings_card = QFrame()
        settings_card.setObjectName("card")
        settings_layout = QVBoxLayout(settings_card)
        settings_layout.setContentsMargins(18, 15, 18, 15)
        settings_layout.setSpacing(12)

        header = QHBoxLayout()
        self.toggle_button = QPushButton()
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(self._expanded)
        self.toggle_button.clicked.connect(self.set_expanded)
        header.addWidget(self.toggle_button)

        title = QLabel(t("ai.title"))
        title.setObjectName("sectionLabel")
        header.addWidget(title)
        header.addStretch()

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setObjectName("pathLabel")
        header.addWidget(self.summary_label, 1)

        self.settings_body = QWidget()
        body_layout = QVBoxLayout(self.settings_body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)

        controls_header = QHBoxLayout()
        controls_header.addWidget(QLabel("Profile"))
        self.profile_combo = QComboBox()
        self.profile_combo.addItems([t("profile.thrifty"), t("profile.standard"), t("profile.deep"), t("profile.custom")])
        self.profile_combo.setMinimumWidth(110)
        self.profile_combo.currentTextChanged.connect(
            self._on_profile_changed
        )
        controls_header.addWidget(self.profile_combo)

        self.save_project_button = QPushButton(t("ai.save_project_default"))
        self.save_project_button.clicked.connect(
            self.save_project_defaults
        )
        controls_header.addWidget(self.save_project_button)

        self.save_global_button = QPushButton(t("ai.save_global_default"))
        self.save_global_button.clicked.connect(
            self.save_global_defaults
        )
        controls_header.addWidget(self.save_global_button)
        controls_header.addStretch()
        settings_layout.addLayout(header)
        body_layout.addLayout(controls_header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(9)

        grid.addWidget(QLabel(run_opts.label_for("workflow_mode")), 0, 0)
        self.workflow_combo = QComboBox()
        # **這兩個字跟陣容頁徽章上的是同一份**（`sequence.*`）。
        # 2026-08-26 之前這裡是 `ai.mode.*`「計畫發想／正式開發」，而徽章寫
        # 「計畫序列／開發序列」——同一件事兩個名字，而使用者要在這兩個
        # 畫面之間對照：跑哪一條在這裡選，那一條下每一格做什麼在陣容頁看。
        self.workflow_combo.addItem(t("sequence.planning"), "planning")
        self.workflow_combo.addItem(t("sequence.development"), "development")
        grid.addWidget(self.workflow_combo, 0, 1)

        grid.addWidget(QLabel(run_opts.label_for("planning_rounds")), 0, 2)
        self.rounds_spin = QSpinBox()
        self.rounds_spin.setRange(0, 5)
        self.rounds_spin.setSuffix(t("unit.rounds"))
        grid.addWidget(self.rounds_spin, 0, 3)

        grid.addWidget(QLabel(run_opts.label_for("claude_model")), 1, 0)
        self.claude_model_combo = QComboBox()
        self.claude_model_combo.setEditable(True)
        self.claude_model_combo.addItem("Claude Sonnet 5", "sonnet")
        self.claude_model_combo.addItem("Claude Opus 5", "opus")
        self.claude_model_combo.addItem("Claude Haiku", "haiku")
        self.claude_model_combo.addItem(t("ai.follow_cli_default"), "default")
        grid.addWidget(self.claude_model_combo, 1, 1)

        grid.addWidget(QLabel(run_opts.label_for("claude_effort")), 1, 2)
        self.claude_effort_combo = QComboBox()
        for label, value in [
            ("Auto", "auto"),
            ("Low", "low"),
            ("Medium", "medium"),
            ("High", "high"),
            ("Extra High", "xhigh"),
            ("Max", "max"),
            ("Ultracode", "ultracode"),
        ]:
            self.claude_effort_combo.addItem(label, value)
        grid.addWidget(self.claude_effort_combo, 1, 3)

        grid.addWidget(QLabel(run_opts.label_for("claude_speed")), 2, 0)
        self.claude_speed_combo = QComboBox()
        self.claude_speed_combo.addItem(
            t("ai.claude.speed_note"),
            "standard",
        )
        self.claude_speed_combo.setEnabled(False)
        grid.addWidget(self.claude_speed_combo, 2, 1)

        grid.addWidget(QLabel(run_opts.label_for("claude_max_turns")), 2, 2)
        self.claude_turns_spin = QSpinBox()
        self.claude_turns_spin.setRange(0, 200)
        self.claude_turns_spin.setSpecialValueText(t("common.unlimited"))
        grid.addWidget(self.claude_turns_spin, 2, 3)

        grid.addWidget(QLabel(run_opts.label_for("claude_permission_mode")), 3, 0)
        self.claude_permission_combo = QComboBox()
        self.claude_permission_combo.addItem(
            t("ai.permission.auto"),
            "auto",
        )
        self.claude_permission_combo.addItem(
            t("ai.permission.edits_only"),
            "acceptEdits",
        )
        self.claude_permission_combo.addItem(
            t("ai.permission.manual"),
            "default",
        )
        self.claude_permission_combo.addItem(
            t("ai.permission.read_only"),
            "plan",
        )
        grid.addWidget(self.claude_permission_combo, 3, 1)

        grid.addWidget(QLabel(run_opts.label_for("claude_timeout_minutes")), 3, 2)
        self.claude_timeout_combo = QComboBox()
        for label, minutes in [
            (t("ai.timeout.10m"), 10),
            (t("ai.timeout.30m"), 30),
            (t("ai.timeout.1h"), 60),
            (t("ai.timeout.3h"), 180),
            (t("ai.timeout.6h"), 360),
            (t("common.unlimited"), 0),
        ]:
            self.claude_timeout_combo.addItem(label, minutes)
        grid.addWidget(self.claude_timeout_combo, 3, 3)

        grid.addWidget(QLabel(run_opts.label_for("codex_model")), 4, 0)
        self.codex_model_combo = QComboBox()
        self.codex_model_combo.setEditable(True)
        self.codex_model_combo.addItem("GPT-5.5", "gpt-5.5")
        self.codex_model_combo.addItem("GPT-5.6 Sol", "gpt-5.6-sol")
        self.codex_model_combo.addItem("GPT-5.6 Terra", "gpt-5.6-terra")
        self.codex_model_combo.addItem("GPT-5.6 Luna", "gpt-5.6-luna")
        self.codex_model_combo.addItem("GPT-5.4 Mini", "gpt-5.4-mini")
        grid.addWidget(self.codex_model_combo, 4, 1)

        grid.addWidget(QLabel(run_opts.label_for("codex_effort")), 4, 2)
        self.codex_effort_combo = QComboBox()
        for label, value in [
            ("Low", "low"),
            ("Medium", "medium"),
            ("High", "high"),
            ("Extra High", "xhigh"),
        ]:
            self.codex_effort_combo.addItem(label, value)
        grid.addWidget(self.codex_effort_combo, 4, 3)

        grid.addWidget(QLabel(run_opts.label_for("codex_speed")), 5, 0)
        self.codex_speed_combo = QComboBox()
        self.codex_speed_combo.addItem(
            t("ai.codex.speed_note"),
            "model_default",
        )
        self.codex_speed_combo.setEnabled(False)
        grid.addWidget(self.codex_speed_combo, 5, 1)

        grid.addWidget(QLabel(run_opts.label_for("claude_verbosity")), 5, 2)
        self.output_length_combo = QComboBox()
        self.output_length_combo.addItem(t("verbosity.concise"), "concise")
        self.output_length_combo.addItem(t("profile.standard"), "standard")
        self.output_length_combo.addItem(t("verbosity.detailed"), "detailed")
        grid.addWidget(self.output_length_combo, 5, 3)

        grid.addWidget(QLabel(run_opts.label_for("context_profile")), 6, 0)
        self.context_profile_combo = QComboBox()
        self.context_profile_combo.setObjectName("contextProfileCombo")
        self.context_profile_combo.addItem(t("ai.context.minimal"), "minimal")
        self.context_profile_combo.addItem(t("ai.context.balanced"), "balanced")
        self.context_profile_combo.addItem(t("ai.context.strict"), "strict")
        grid.addWidget(self.context_profile_combo, 6, 1)

        # ⛔ 2026-09-01 之前這一行是 `(6, 0)`，**而上下文範圍的標籤已經在那一格**。
        # 後加的蓋掉前面那個：「上下文範圍」旁邊寫著「自動 FIX」，
        # 而下面那個數字框（真正的自動 FIX）一個標籤都沒有。
        # 兩個都畫得出來、沒有一支測試看得到——`test_every_control_says_what_it_is`
        # 是這一輪補的那一條。
        grid.addWidget(QLabel(run_opts.label_for("max_fix_attempts")), 7, 0)
        self.fix_spin = QSpinBox()
        self.fix_spin.setRange(0, 2)
        self.fix_spin.setSuffix(t("unit.times"))
        grid.addWidget(self.fix_spin, 7, 1)

        self.force_new_checkbox = QCheckBox(
            t("ai.force_new_sessions")
        )
        self.force_new_checkbox.setChecked(True)
        grid.addWidget(self.force_new_checkbox, 7, 2, 1, 2)

        self.auto_bootstrap_checkbox = QCheckBox(
            t("ai.auto_bootstrap")
        )
        self.auto_bootstrap_checkbox.setChecked(True)
        grid.addWidget(self.auto_bootstrap_checkbox, 8, 0, 1, 4)

        self.auto_memory_checkbox = QCheckBox(
            t("ai.auto_memory")
        )
        self.auto_memory_checkbox.setObjectName("autoMemoryMaintenanceCheckbox")
        self.auto_memory_checkbox.setChecked(True)
        grid.addWidget(self.auto_memory_checkbox, 9, 0, 1, 4)

        body_layout.addLayout(grid)
        settings_layout.addWidget(self.settings_body)
        root.addWidget(settings_card)

        task_card = QFrame()
        task_card.setObjectName("card")
        task_layout = QVBoxLayout(task_card)
        task_layout.setContentsMargins(18, 15, 18, 15)
        task_layout.setSpacing(9)

        task_header = QHBoxLayout()
        task_title = QLabel(t("task.title"))
        task_title.setObjectName("sectionLabel")
        task_header.addWidget(task_title)
        task_header.addStretch()

        self.save_task_button = QPushButton(t("task.save"))
        self.save_task_button.clicked.connect(self.save_task)
        task_header.addWidget(self.save_task_button)
        task_layout.addLayout(task_header)

        self.task_input = QPlainTextEdit()
        self.task_input.setMinimumHeight(130)
        self.task_input.setPlaceholderText(
            t(
                "task.placeholder",
                planning=t("sequence.planning"),
                development=t("sequence.development"),
            )
        )
        task_layout.addWidget(self.task_input)

        note = QLabel(
            t(
                "task.help",
                planning=t("sequence.planning"),
                development=t("sequence.development"),
            )
        )
        note.setWordWrap(True)
        note.setObjectName("pathLabel")
        task_layout.addWidget(note)
        self.task_card = task_card
        root.addWidget(self.task_card)

        controls = [
            self.workflow_combo,
            self.rounds_spin,
            self.claude_model_combo,
            self.claude_effort_combo,
            self.claude_permission_combo,
            self.claude_timeout_combo,
            self.claude_turns_spin,
            self.codex_model_combo,
            self.codex_effort_combo,
            self.output_length_combo,
            self.context_profile_combo,
            self.fix_spin,
            self.force_new_checkbox,
            self.auto_bootstrap_checkbox,
            self.auto_memory_checkbox,
        ]
        for control in controls:
            if isinstance(control, QComboBox):
                control.currentIndexChanged.connect(self._on_field_changed)
                if control.isEditable():
                    control.currentTextChanged.connect(
                        self._on_field_changed
                    )
            elif isinstance(control, QSpinBox):
                control.valueChanged.connect(self._on_field_changed)
            elif isinstance(control, QCheckBox):
                control.toggled.connect(self._on_field_changed)


    @Slot(bool)
    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self._settings.setValue(
            "ai_settings_panel/expanded",
            self._expanded,
        )
        if hasattr(self, "settings_body"):
            self.settings_body.setVisible(self._expanded)
        if hasattr(self, "task_card"):
            self.task_card.setVisible(self._expanded)
        if hasattr(self, "toggle_button"):
            self.toggle_button.setChecked(self._expanded)
            self.toggle_button.setText(
                t("common.collapse") if self._expanded else t("common.expand")
            )

    def is_expanded(self) -> bool:
        return self._expanded

    def set_project(self, project_path: Path) -> None:
        self.project_path = project_path.expanduser().resolve()
        self.store.set_project(self.project_path)
        self._ensure_git_local_exclude()
        self.apply_settings(self.store.load_project())
        self.load_task()

    def set_planning_task(
        self,
        text: str,
        planning_rounds: int = 1,
    ) -> None:
        self._loading = True
        try:
            self._set_combo_data(self.workflow_combo, "planning")
            self.rounds_spin.setValue(max(0, min(5, planning_rounds)))
            self.profile_combo.setCurrentText(t("profile.custom"))
            self.task_input.setPlainText(text.strip())
        finally:
            self._loading = False
        self._update_enabled_state()
        # ⚠️ **要寫進硬碟，不能只留在 widget 上。**
        #
        # 2026-09-01 這個面板從主視窗的版面上拿掉了（設定搬進陣容頁），
        # 於是「只存在 widget 上」＝**只存在一個沒有人看得到的地方**：
        # 他被告知「已切換成計畫模式」，而陣容頁上那顆任務入口是空的，
        # 執行設定那一段還寫著開發序列。
        #
        # 寫進硬碟之後兩邊讀的是同一份，而這一支本來就要求存
        # （`save_task()` 執行時也是寫同一個檔）。
        self.store.save_project(self.current_settings())
        self.save_task(quiet=True)
        self._update_summary()
        self.settings_changed.emit(self.current_settings())

    def load_task(self) -> None:
        planning = self.workflow_combo.currentData() == "planning"
        file_name = "planning-request.md" if planning else "task.md"
        task_path = self.project_path / ".aicc" / file_name
        fallback_path = self.project_path / ".aicc" / "task.md"
        try:
            text = task_path.read_text(encoding="utf-8")
        except OSError:
            try:
                text = fallback_path.read_text(encoding="utf-8")
            except OSError:
                text = f"{prompt_markers.TASK_HEADING}\n\n"
        self.task_input.setPlainText(text.rstrip())

    def refresh_from_disk(self) -> None:
        """把設定與任務**從硬碟重讀一次**。

        2026-09-01 之前不需要這一支：這個面板是使用者唯一改設定的地方，
        widget 上的值就是事實。

        現在不是了——AI 執行設定搬進陣容頁之後（老闆 2026-08-27：
        「AI 執行設定那邊一樣吃到頁面」），**同一份設定有兩個入口**，
        而網頁那一個寫的是硬碟（`AISettingsStore` 與 `.aicc/task.md`）。

        > 不重讀的話會發生這件事：他在網頁把模型改成 opus、把任務打好，
        > 按下開始執行——而 `current_settings()` 回的是這個面板上那份沒動過
        > 的舊值，`save_task()` 還會**拿它那份空的任務蓋掉他剛剛寫的**。
        > 空的任務會擋下整輪，而畫面上他明明看到自己打的字。

        `set_project()` 本來就做這兩件（換專案的時候），這裡把它們單獨拿出來
        ——**同一個實作，不是第二份**。
        """
        self.apply_settings(self.store.load_project())
        self.load_task()

    def _ensure_git_local_exclude(self) -> None:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "--git-path", "info/exclude"],
                cwd=self.project_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
                creationflags=NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            return

        if completed.returncode != 0:
            return

        raw_path = completed.stdout.strip()
        if not raw_path:
            return
        exclude_path = Path(raw_path)
        if not exclude_path.is_absolute():
            exclude_path = self.project_path / exclude_path

        try:
            exclude_path.parent.mkdir(parents=True, exist_ok=True)
            existing = (
                exclude_path.read_text(encoding="utf-8")
                if exclude_path.is_file()
                else ""
            )
            rules = {line.strip() for line in existing.splitlines()}
            if ".aicc/" not in rules:
                prefix = existing
                if prefix and not prefix.endswith("\n"):
                    prefix += "\n"
                exclude_path.write_text(
                    prefix + "\n# AI Control Center local state\n.aicc/\n",
                    encoding="utf-8",
                )
        except OSError:
            return

    @Slot()
    def save_task(self, quiet: bool = False) -> bool:
        """把任務寫進硬碟。

        `quiet=True` 是**程式呼叫**用的：不跳對話框，只回 False。
        跳框那一版是給「他按下開始執行」用的——那時候他人在螢幕前面，
        而一個沒有題目的任務要當面擋住他。
        """
        text = self.task_input.toPlainText().strip()
        if not text:
            if not quiet:
                QMessageBox.warning(
                    self,
                    t("task.empty.title"),
                    t("task.empty.body"),
                )
            return False

        planning = self.workflow_combo.currentData() == "planning"
        file_name = "planning-request.md" if planning else "task.md"
        task_path = self.project_path / ".aicc" / file_name
        try:
            task_path.parent.mkdir(parents=True, exist_ok=True)
            task_path.write_text(text + "\n", encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(
                self,
                t("common.save_failed"),
                f"無法寫入 {file_name}：\n{exc}",
            )
            return False
        return True

    def current_settings(self) -> AIExecutionSettings:
        output_length = str(
            self.output_length_combo.currentData() or "concise"
        )
        return AIExecutionSettings(
            workflow_mode=str(
                self.workflow_combo.currentData() or "planning"
            ),
            planning_rounds=self.rounds_spin.value(),
            claude_model=self._combo_value(
                self.claude_model_combo,
                "sonnet",
            ),
            claude_effort=str(
                self.claude_effort_combo.currentData() or "medium"
            ),
            claude_speed="standard",
            claude_permission_mode=str(
                self.claude_permission_combo.currentData() or "auto"
            ),
            claude_timeout_minutes=int(
                self.claude_timeout_combo.currentData() or 0
            ),
            claude_max_turns=self.claude_turns_spin.value(),
            claude_verbosity=output_length,
            codex_model=self._combo_value(
                self.codex_model_combo,
                "gpt-5.5",
            ),
            codex_effort=str(
                self.codex_effort_combo.currentData() or "medium"
            ),
            codex_speed="model_default",
            codex_verbosity={
                "concise": "low",
                "standard": "medium",
                "detailed": "high",
            }.get(output_length, "low"),
            max_fix_attempts=self.fix_spin.value(),
            force_new_sessions=self.force_new_checkbox.isChecked(),
            auto_session_bootstrap=self.auto_bootstrap_checkbox.isChecked(),
            auto_memory_maintenance=self.auto_memory_checkbox.isChecked(),
            context_profile=str(
                self.context_profile_combo.currentData() or "minimal"
            ),
            profile_name=self.profile_combo.currentText(),
        ).normalized()

    def apply_settings(self, settings: AIExecutionSettings) -> None:
        settings = settings.normalized()
        self._loading = True
        try:
            self._set_combo_text(self.profile_combo, settings.profile_name)
            self._set_combo_data(
                self.workflow_combo,
                settings.workflow_mode,
            )
            self.rounds_spin.setValue(settings.planning_rounds)
            self._set_editable_combo(
                self.claude_model_combo,
                settings.claude_model,
            )
            self._set_combo_data(
                self.claude_effort_combo,
                settings.claude_effort,
            )
            self._set_combo_data(
                self.claude_permission_combo,
                settings.claude_permission_mode,
            )
            self._set_combo_data(
                self.claude_timeout_combo,
                settings.claude_timeout_minutes,
            )
            self.claude_turns_spin.setValue(settings.claude_max_turns)
            self._set_editable_combo(
                self.codex_model_combo,
                settings.codex_model,
            )
            self._set_combo_data(
                self.codex_effort_combo,
                settings.codex_effort,
            )
            self._set_combo_data(
                self.output_length_combo,
                settings.claude_verbosity,
            )
            self._set_combo_data(
                self.context_profile_combo,
                settings.context_profile,
            )
            self.fix_spin.setValue(settings.max_fix_attempts)
            self.force_new_checkbox.setChecked(
                settings.force_new_sessions
            )
            self.auto_bootstrap_checkbox.setChecked(
                settings.auto_session_bootstrap
            )
            self.auto_memory_checkbox.setChecked(
                settings.auto_memory_maintenance
            )
        finally:
            self._loading = False
        self._update_enabled_state()
        self._update_summary()
        self.set_expanded(self._expanded)

    @Slot(str)
    def _on_profile_changed(self, profile_name: str) -> None:
        """套用一個內建組合，**但不動權限模式**。

        三個組合（省額度／標準／深入）是**額度**預設：模型、推理強度、輪數、
        上下文大小。它們原本每一個都寫死 `claude_permission_mode="auto"`，
        而 `apply_settings` 覆蓋每一個欄位——所以會發生這件事：

            在設定頁把某個客戶專案設成「手動授權」，接著在這個面板碰一下
            組合下拉選單，它**無聲地變回「安全自動」**。

        沒有任何地方會講。而網頁設定頁自己在「全自動」旁邊印著
        「客戶或 KYC 專案請維持每次問」——兩個面板寫的是同一份設定。

        安全姿態跟著使用者走，不跟著額度預設走。要改權限就自己去改那一格。
        """
        if self._loading or profile_name == t("profile.custom"):
            return
        preset = AISettingsStore.profile(profile_name)
        # 沒有 `or preset.claude_permission_mode` 那個 fallback：四個項目是在
        # `__init__` 加進去的、沒有任何地方清空它們，所以 `currentData()` 一定
        # 回得出值。負向驗證證明過那個 fallback 到不了（拿掉它沒有任何測試變紅），
        # 而一段到不了的防禦碼會讓人以為某個情況有被處理。
        preset.claude_permission_mode = self.claude_permission_combo.currentData()
        self.apply_settings(preset)

    @Slot()
    def _on_field_changed(self, *args) -> None:
        del args
        if self._loading:
            return
        self._loading = True
        try:
            self.profile_combo.setCurrentText(t("profile.custom"))
        finally:
            self._loading = False
        self._update_enabled_state()
        self._update_summary()
        self.settings_changed.emit(self.current_settings())

    def _update_enabled_state(self) -> None:
        planning = self.workflow_combo.currentData() == "planning"
        self.rounds_spin.setEnabled(planning and not self._busy)
        self.fix_spin.setEnabled((not planning) and not self._busy)
        self.claude_permission_combo.setEnabled(
            (not planning) and not self._busy
        )
        self.save_task_button.setText(
            t("task.save_plan") if planning else t("task.save")
        )

    def _update_summary(self) -> None:
        """那一行「本次：⋯」。

        **內容不在這裡組**——2026-08-27 抽進 `app/services/run_summary.py`，
        因為網頁那一段也要同一行字。兩份的話它們會漂，而漂掉的樣子
        （兩行都還在、都還讀得通、只是講的不是同一件事）在畫面上
        一個字都看不出來。
        """
        self.summary_label.setText(summary_line(self.current_settings()))

    @Slot()
    def save_project_defaults(self) -> None:
        try:
            self.store.save_project(self.current_settings())
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, t("common.save_failed"), str(exc))
            return
        QMessageBox.information(
            self,
            t("common.saved"),
            t("ai.saved_as_project_default"),
        )

    @Slot()
    def save_global_defaults(self) -> None:
        try:
            self.store.save_global(self.current_settings())
        except OSError as exc:
            QMessageBox.critical(self, t("common.save_failed"), str(exc))
            return
        QMessageBox.information(
            self,
            t("common.saved"),
            t("ai.saved_as_global_default"),
        )

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        controls = [
            self.profile_combo,
            self.workflow_combo,
            self.claude_model_combo,
            self.claude_effort_combo,
            self.claude_permission_combo,
            self.claude_timeout_combo,
            self.claude_turns_spin,
            self.codex_model_combo,
            self.codex_effort_combo,
            self.output_length_combo,
            self.force_new_checkbox,
            self.auto_bootstrap_checkbox,
            self.auto_memory_checkbox,
            self.save_project_button,
            self.save_global_button,
            self.save_task_button,
            self.task_input,
        ]
        for control in controls:
            control.setEnabled(not busy)
        self._update_enabled_state()

    @staticmethod
    def _combo_value(combo: QComboBox, fallback: str) -> str:
        text = combo.currentText().strip()
        if combo.isEditable():
            matching_index = combo.findText(text)
            if matching_index >= 0:
                data = combo.itemData(matching_index)
                if data:
                    return str(data)
            return text or fallback

        data = combo.currentData()
        if data:
            return str(data)
        return text or fallback

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _set_combo_text(combo: QComboBox, value: str) -> None:
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)
        else:
            combo.setCurrentText(value)

    @staticmethod
    def _set_editable_combo(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)
        else:
            combo.setEditText(value)
