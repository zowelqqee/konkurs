"""Streamlit-интерфейс мультивузового анализатора конкурсных списков.

Здесь только интерфейс. Вся логика — в пакете `analyzer` и работает без него.
Запуск:  streamlit run app.py
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import streamlit as st

from analyzer import (
    ApplicantKey,
    CROSS_SCENARIO_LABELS,
    CompetitionCategory,
    INTRA_SCENARIOS,
    INTRA_SCENARIO_LABELS,
    ProgramConfig,
    ScenarioConfig,
    Status,
    allocation,
    cross_university,
    discovery,
    reports,
    validation,
)

logging.basicConfig(level=logging.INFO)

st.set_page_config(page_title="Мультивузовый анализ конкурсных списков", layout="wide")

PROGRAM_CONFIG_PATH = Path("config/programs.json")
PREFERENCES_PATH = Path("config/university_preferences.json")

COLORS = {
    "green": "#1a7f37", "yellow": "#9a6700", "red": "#b42318",
    "grey": "#6e7781", "blue": "#0969da", "purple": "#8250df",
}
BACKGROUNDS = {
    "green": "#e6f4ea", "yellow": "#fff8e1", "red": "#fdecea",
    "grey": "#f1f3f5", "blue": "#e8f1fd", "purple": "#f3ecfd",
}

PASSING_INTRA_STATUSES = {Status.ALLOCATED.value, Status.BVI.value, Status.QUOTA.value}


# --------------------------------------------------------------------------
# Состояние
# --------------------------------------------------------------------------


def _init_state() -> None:
    st.session_state.setdefault("discovery", None)
    st.session_state.setdefault("configs", {})
    st.session_state.setdefault("computed", None)


def _store_discovery(result) -> None:
    saved = discovery.load_program_configs(PROGRAM_CONFIG_PATH)
    configs = {}
    for pid, metadata in result.programs.items():
        configs[pid] = saved.get(pid) or discovery.default_program_config(metadata)
    st.session_state["discovery"] = result
    st.session_state["configs"] = configs
    st.session_state["computed"] = None


# --------------------------------------------------------------------------
# Раскраска
# --------------------------------------------------------------------------


def _status_color(status: str, margin, window: int) -> str:
    if status == Status.ALLOCATED_HIGHER.value:
        return "grey"
    if status in PASSING_INTRA_STATUSES:
        if margin is not None and margin == margin and abs(margin) <= window:
            return "yellow"
        return "green"
    if status == Status.NOT_ALLOCATED.value:
        if margin is not None and margin == margin and abs(margin) <= window:
            return "yellow"
        return "red"
    return "grey"


def _style_status_rows(df: pd.DataFrame, window: int, margins: pd.Series | None, status_col: str):
    def row_style(row: pd.Series) -> list[str]:
        status = str(row.get(status_col, ""))
        margin = margins.get(row.name) if margins is not None else None
        color = _status_color(status, margin, window)
        return [f"background-color: {BACKGROUNDS[color]}; color: {COLORS[color]}"] * len(row)

    return df.style.apply(row_style, axis=1)


def _cross_color(cross) -> str:
    if cross is None:
        return "grey"
    passing = [o for o in cross.offers if o.status == cross_university.PASSING_STATUS]
    if len(passing) > 1:
        return "purple" if cross.ambiguous else "blue"
    if len(passing) == 1:
        return "green"
    return "red"


def _legend() -> None:
    st.markdown(
        f"""
<div style="display:flex;gap:1.2rem;flex-wrap:wrap;font-size:0.85rem">
  <span style="color:{COLORS['green']}">■ проходит</span>
  <span style="color:{COLORS['yellow']}">■ пограничная позиция</span>
  <span style="color:{COLORS['red']}">■ не проходит</span>
  <span style="color:{COLORS['grey']}">■ ушёл на более высокий приоритет внутри вуза</span>
  <span style="color:{COLORS['blue']}">■ проходит одновременно в нескольких вузах</span>
  <span style="color:{COLORS['purple']}">■ межвузовский выбор неизвестен</span>
</div>
""",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------
# Боковая панель
# --------------------------------------------------------------------------


def sidebar():
    st.sidebar.header("1. Данные")
    campaign_year = st.sidebar.number_input("Год кампании", 2000, 2100, discovery.DEFAULT_CAMPAIGN_YEAR, step=1)
    source = st.sidebar.radio("Источник", ["Папка", "Загрузить файлы"], horizontal=True)

    if source == "Папка":
        folder = st.sidebar.text_input("Путь к папке", value="data")
        if st.sidebar.button("Загрузить из папки", width="stretch"):
            try:
                result = discovery.load_directory(folder, campaign_year=int(campaign_year))
                _store_discovery(result)
                st.sidebar.success(f"Загружено программ: {len(result.programs)} из {len(result.files)} файлов")
            except discovery.ReaderError as exc:
                st.sidebar.error(str(exc))
    else:
        uploads = st.sidebar.file_uploader(
            "Файлы .tsv / .csv / .xlsx", type=["tsv", "csv", "xlsx", "xlsm"], accept_multiple_files=True
        )
        if uploads and st.sidebar.button("Загрузить файлы", width="stretch"):
            result = discovery.load_uploads(uploads, campaign_year=int(campaign_year))
            _store_discovery(result)
            st.sidebar.success(f"Загружено программ: {len(result.programs)} из {len(result.files)} файлов")

    st.sidebar.header("2. Внутривузовский сценарий")
    intra_key = st.sidebar.selectbox(
        "Сценарий", options=list(INTRA_SCENARIO_LABELS), format_func=lambda k: INTRA_SCENARIO_LABELS[k], index=1
    )
    intra_scenario = ScenarioConfig(**INTRA_SCENARIOS[intra_key].to_dict())
    if intra_key == "custom":
        intra_scenario.respect_consent = st.sidebar.checkbox("Учитывать только согласия", value=False)
        intra_scenario.count_bvi = st.sidebar.checkbox("Учитывать БВИ", value=True)
        intra_scenario.use_quotas = st.sidebar.checkbox("Учитывать квоты", value=True)
        intra_scenario.redistribute_unfilled_quota = st.sidebar.checkbox("Возвращать незаполненные квоты", value=True)
        intra_scenario.include_under_review = st.sidebar.checkbox("Учитывать «На рассмотрении» (ВШЭ)", value=False)
        unlimited = st.sidebar.checkbox("Все приоритеты", value=True)
        intra_scenario.max_priority = None if unlimited else st.sidebar.number_input("Макс. приоритет", 1, 15, 3, step=1)

    st.sidebar.header("3. Межвузовский сценарий")
    cross_key = st.sidebar.selectbox(
        "Сценарий", options=list(CROSS_SCENARIO_LABELS), format_func=lambda k: CROSS_SCENARIO_LABELS[k], index=1
    )

    prefs = cross_university.load_university_preferences(PREFERENCES_PATH)
    probabilistic_weights: dict[str, float] = {}
    if cross_key == "preference_based":
        discovery_result = st.session_state.get("discovery")
        universities = sorted(discovery_result.universities()) if discovery_result else []
        default_order = st.sidebar.multiselect(
            "Порядок предпочтений по умолчанию (первый — приоритетнее)",
            options=universities,
            default=[u for u in prefs.get("default", []) if u in universities],
        )
        prefs["default"] = default_order
        if st.sidebar.button("💾 Сохранить предпочтения"):
            cross_university.save_university_preferences(prefs, PREFERENCES_PATH)
            st.sidebar.success("Сохранено")
    elif cross_key == "probabilistic":
        discovery_result = st.session_state.get("discovery")
        universities = sorted(discovery_result.universities()) if discovery_result else []
        st.sidebar.caption("Экспериментальный режим: не официальный прогноз, а Monte Carlo-иллюстрация.")
        for uni in universities:
            probabilistic_weights[uni] = st.sidebar.slider(f"Вероятность выбора: {uni}", 0.0, 1.0, 0.5, 0.05)

    st.sidebar.header("4. Абитуриент")
    code = st.sidebar.text_input("Ваш уникальный код (Госуслуги)", value="").strip()

    st.sidebar.header("5. Отображение")
    window = st.sidebar.number_input("Пограничная зона, ± мест", 0, 100, 5, step=1)
    intra_scenario.borderline_window = int(window)

    return campaign_year, intra_scenario, cross_key, prefs, probabilistic_weights, code, int(window)


# --------------------------------------------------------------------------
# Найденные файлы + настройки программ
# --------------------------------------------------------------------------


def show_files_and_warnings(result) -> None:
    st.subheader("Найденные файлы")
    st.dataframe(reports.files_table(result), hide_index=True, width="stretch")

    errors = [w for w in result.warnings if w.severity == "error"]
    for w in errors:
        st.error(w.message)


def programs_editor(result, configs: dict[str, ProgramConfig]) -> None:
    st.subheader("Настройки программ")
    st.caption("Число мест вуз не всегда публикует в списках — проверьте и задайте вручную. Настройки сохраняются в config/programs.json.")

    rows = []
    for pid, metadata in sorted(result.programs.items()):
        cfg = configs[pid]
        rows.append(
            {
                "program_id": pid,
                "Вуз": metadata.university_name,
                "Программа": metadata.title,
                "Строк": len(result.applications.get(pid, [])),
                "Всего мест": cfg.total_places,
                "Особая квота": cfg.special_quota_places,
                "Отдельная квота": cfg.separate_quota_places,
                "Целевая квота": cfg.target_quota_places,
                "БВИ внутри бюджета": cfg.bvi_within_budget,
                "Учитывать «На рассмотрении»": cfg.include_under_review,
            }
        )
    edited = st.data_editor(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        disabled=["program_id", "Вуз", "Программа", "Строк"],
        column_config={
            "Всего мест": st.column_config.NumberColumn(min_value=0, step=1),
            "Особая квота": st.column_config.NumberColumn(min_value=0, step=1),
            "Отдельная квота": st.column_config.NumberColumn(min_value=0, step=1),
            "Целевая квота": st.column_config.NumberColumn(min_value=0, step=1),
        },
        key="programs_editor",
    )
    for _, row in edited.iterrows():
        cfg = configs[row["program_id"]]
        cfg.total_places = int(row["Всего мест"])
        cfg.special_quota_places = int(row["Особая квота"])
        cfg.separate_quota_places = int(row["Отдельная квота"])
        cfg.target_quota_places = int(row["Целевая квота"])
        cfg.bvi_within_budget = bool(row["БВИ внутри бюджета"])
        cfg.include_under_review = bool(row["Учитывать «На рассмотрении»"])

    if st.button("💾 Сохранить настройки программ"):
        discovery.save_program_configs(configs, PROGRAM_CONFIG_PATH)
        st.success(f"Сохранено в {PROGRAM_CONFIG_PATH}")


# --------------------------------------------------------------------------
# Сводки
# --------------------------------------------------------------------------


def show_university_summary(intra_results, discovery_result) -> None:
    st.subheader("Сводка по вузам")
    rows = []
    for uni, result in sorted(intra_results.items()):
        total_seats = sum(pr.seats_total for pr in result.programs.values())
        allocated = sum(pr.allocated_count for pr in result.programs.values())
        rows.append(
            {
                "Вуз": uni,
                "Программ": len(result.programs),
                "Мест всего": total_seats,
                "Распределено": allocated,
                "Свободно": max(total_seats - allocated, 0),
                "Итераций алгоритма": result.iterations,
                "Сошлось": "да" if result.converged else "НЕТ",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def show_programs_summary(discovery_result, configs, intra_results, cross_results) -> None:
    st.subheader("Сводка по программам")
    df = reports.programs_table(discovery_result.programs, configs, intra_results, cross_results)
    st.dataframe(df, hide_index=True, width="stretch")


# --------------------------------------------------------------------------
# Профиль абитуриента
# --------------------------------------------------------------------------


def show_applicant_profile(computed, code: str, campaign_year: int, scenario, window: int) -> None:
    key = ApplicantKey(campaign_year=int(campaign_year), applicant_id=code)
    profiles = computed["profiles"]
    if key not in profiles:
        st.warning(f"Код {code} (год {campaign_year}) не найден ни в одном из загруженных вузов.")
        return

    profile = profiles[key]
    cross = computed["cross_results"].get(key)
    discovery_result = computed["discovery"]

    st.subheader(f"Профиль абитуриента {code}")
    if cross and cross.selected_offer:
        st.success(
            f"Межвузовский выбор: {cross.selected_offer.university_code} "
            f"({discovery_result.programs[cross.selected_offer.program_id].title})"
        )
    elif cross:
        st.warning(f"Межвузовский выбор: {cross.selection_reason}")

    df = reports.applicant_report(
        key, profile, discovery_result.programs, computed["intra_results"], cross, scenario
    )
    if df.empty:
        st.info("Нет заявлений для отображения.")
    else:
        st.dataframe(
            _style_status_rows(df, window, df["Запас / дефицит"], "Итог внутривузового распределения"),
            hide_index=True, width="stretch",
        )
        _legend()

    st.markdown("**Объяснение по каждой программе**")
    apps_by_program: dict[str, list] = {}
    for app in profile.applications:
        apps_by_program.setdefault(app.program_id, []).append(app)
    for program_id in sorted(apps_by_program):
        text = reports.explain_intra(
            key, program_id, discovery_result.programs, apps_by_program, computed["intra_results"], scenario
        )
        st.markdown(f"- {text}")

    if cross:
        st.markdown("**Межвузовское объяснение**")
        st.markdown(reports.explain_cross(cross))


# --------------------------------------------------------------------------
# Рейтинг программы
# --------------------------------------------------------------------------


def show_program_ranking(computed, scenario, window: int, code: str) -> None:
    st.subheader("Рейтинг программы")
    discovery_result = computed["discovery"]
    programs = discovery_result.programs
    if not programs:
        st.info("Нет загруженных программ.")
        return

    pid = st.selectbox("Программа", options=sorted(programs), format_func=lambda p: f"{programs[p].university_name} — {programs[p].title}")
    metadata = programs[pid]
    result = computed["intra_results"].get(metadata.university_code)
    if result is None:
        st.info("Для этого вуза нет результата расчёта.")
        return

    tracks = [t for (p, t) in result.rankings if p == pid]
    if not tracks:
        st.info("Для этой программы нет строк, участвующих в распределении.")
        return
    track = st.selectbox("Конкурс", options=tracks, format_func=lambda t: "БВИ" if t == "bvi" else t)

    ranked = result.rankings.get((pid, track), [])
    pr = result.programs[pid]
    capacity = pr.bvi_count if track == "bvi" else (pr.general_seats if track == CompetitionCategory.GENERAL.value else pr.quota_filled.get(track, 0))
    st.caption(f"Мест в этом конкурсе: {capacity} · строк в рейтинге: {len(ranked)}")

    rows = []
    for i, app in enumerate(ranked, start=1):
        target = result.assignment.get(app.applicant_key)
        if target == pid:
            status = "проходит на эту программу"
        elif target is not None:
            status = "проходит на программу с более высоким приоритетом"
        else:
            status = "не проходит"
        rows.append(
            {
                "Позиция": i, "Код": app.code, "Приоритет": app.priority, "Балл": app.total_score,
                "Согласие": "да" if app.consent else ("нет" if app.consent is False else ""),
                "Исходное место": app.source_rank, "Статус": status,
            }
        )
    df = pd.DataFrame(rows)
    if code:
        mine = df[df["Код"] == code]
        if not mine.empty:
            st.info(f"Ваша позиция: {int(mine.iloc[0]['Позиция'])}")
    margins = capacity - df["Позиция"] if not df.empty else None
    st.dataframe(_style_status_rows(df, window, margins, "Статус"), hide_index=True, width="stretch", height=450)
    _legend()


# --------------------------------------------------------------------------
# Вероятностный сценарий
# --------------------------------------------------------------------------


def show_probabilistic(computed, scenario, weights: dict[str, float]) -> None:
    st.subheader("Вероятностный сценарий (Monte Carlo)")
    st.caption(
        "ЭКСПЕРИМЕНТАЛЬНО: не официальный прогноз, а иллюстрация того, как менялась бы картина "
        "при заданных вами вероятностях выбора вуза. Минимум 1000 итераций."
    )
    n = st.number_input("Число итераций", 1000, 20000, 1000, step=500)
    if st.button("🎲 Запустить симуляцию"):
        discovery_result = computed["discovery"]
        with st.spinner(f"Считаем {n} итераций…"):
            prob = cross_university.simulate_probabilistic(
                discovery_result.programs,
                computed["configs"],
                discovery_result.applications,
                scenario,
                weights,
                computed["profiles"],
                computed["intra_results"],
                computed["cross_results"],
                n_iterations=int(n),
            )
        st.session_state["probabilistic"] = prob

    prob = st.session_state.get("probabilistic")
    if prob is None:
        return
    st.write(f"Итераций: {prob.iterations}")
    rows = [{"Код": code, "Вероятность пройти": round(p, 3)} for code, p in prob.applicant_pass_probability.items()]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    program_rows = []
    for pid, samples in prob.program_score_samples.items():
        if not samples:
            continue
        series = pd.Series(samples)
        program_rows.append(
            {
                "Программа": pid, "Медиана проходного": series.median(),
                "5-й перцентиль": series.quantile(0.05), "95-й перцентиль": series.quantile(0.95),
                "Среднее свободных мест": round(prob.program_average_freed_seats.get(pid, 0), 2),
            }
        )
    st.dataframe(pd.DataFrame(program_rows), hide_index=True, width="stretch")


# --------------------------------------------------------------------------
# Проверки и экспорт
# --------------------------------------------------------------------------


def show_warnings(warnings) -> None:
    errors = [w for w in warnings if w.severity == "error"]
    plain = [w for w in warnings if w.severity == "warning"]
    infos = [w for w in warnings if w.severity == "info"]
    st.subheader(f"Проверки данных ({len(warnings)})")
    for w in errors:
        st.error(w.message)
    with st.expander(f"Предупреждения ({len(plain)})"):
        for w in plain:
            st.warning(w.message)
    with st.expander(f"Информация ({len(infos)})"):
        for w in infos:
            st.info(w.message)


def show_exports(computed, warnings, scenario, code: str, campaign_year: int) -> None:
    st.subheader("Экспорт")
    discovery_result = computed["discovery"]
    key = ApplicantKey(campaign_year=int(campaign_year), applicant_id=code) if code else None

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.download_button(
            "📊 Excel (все листы)",
            data=reports.to_excel_bytes(
                discovery_result, computed["configs"], computed["intra_results"], computed["profiles"],
                computed["cross_results"], warnings, scenario, key,
            ),
            file_name="cross_university_result.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )
    with col2:
        st.download_button(
            "📄 Программы CSV",
            data=reports.to_csv_bytes(
                reports.programs_table(discovery_result.programs, computed["configs"], computed["intra_results"], computed["cross_results"])
            ),
            file_name="programs.csv", mime="text/csv", width="stretch",
        )
    with col3:
        user_df = (
            reports.applicant_report(
                key, computed["profiles"][key], discovery_result.programs, computed["intra_results"],
                computed["cross_results"].get(key), scenario,
            )
            if key and key in computed["profiles"]
            else pd.DataFrame({"Сообщение": ["Код не задан или не найден"]})
        )
        st.download_button(
            "👤 Отчёт по абитуриенту CSV", data=reports.to_csv_bytes(user_df),
            file_name=f"applicant_{code or 'none'}.csv", mime="text/csv", disabled=not code, width="stretch",
        )
    with col4:
        st.download_button(
            "🧾 JSON симуляции",
            data=reports.to_json_bytes(
                discovery_result, computed["configs"], computed["intra_results"], computed["cross_results"], warnings
            ),
            file_name="simulation.json", mime="application/json", width="stretch",
        )


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main() -> None:
    _init_state()
    st.title("Мультивузовый анализ конкурсных списков")
    st.caption(
        "Прогноз распределения по приоритетам внутри каждого вуза + анализ межвузовского выбора "
        "по глобальному коду Госуслуг. Это модель, а не официальный результат — см. README."
    )

    campaign_year, intra_scenario, cross_key, prefs, weights, code, window = sidebar()
    result = st.session_state.get("discovery")

    if result is None:
        st.info("Загрузите файлы конкурсных списков через боковую панель. Поддерживаются .tsv, .csv, .xlsx.")
        return

    show_files_and_warnings(result)
    configs = st.session_state["configs"]
    programs_editor(result, configs)

    if st.button("🧮 Рассчитать", type="primary", width="stretch"):
        with st.spinner("Считаем внутривузовское и межвузовское распределение…"):
            intra_results = allocation.allocate_all(result.programs, configs, result.applications, intra_scenario)
            profiles = cross_university.build_applicant_profiles(result.applications)
            cross_results = cross_university.resolve_all(profiles, intra_results, cross_key, prefs)

            warnings = list(result.warnings)
            warnings.extend(validation.validate_configs(result.programs, configs))
            warnings.extend(validation.validate_bvi_capacity(result.programs, configs, result.applications))
            for r in intra_results.values():
                warnings.extend(r.warnings)
                warnings.extend(validation.validate_intra_result(r))

        st.session_state["computed"] = {
            "discovery": result, "configs": configs, "intra_results": intra_results,
            "profiles": profiles, "cross_results": cross_results, "warnings": warnings,
            "scenario": intra_scenario,
        }

    computed = st.session_state.get("computed")
    if computed is None:
        st.info("Задайте число мест и нажмите «Рассчитать».")
        return

    scenario = computed["scenario"]
    warnings = computed["warnings"]

    tab_names = ["Сводка по вузам", "Сводка по программам", "Абитуриент", "Рейтинг программы", "Проверки", "Экспорт"]
    if cross_key == "probabilistic":
        tab_names.insert(4, "Вероятностный сценарий")
    tabs = st.tabs(tab_names)
    idx = 0
    with tabs[idx]:
        show_university_summary(computed["intra_results"], result)
    idx += 1
    with tabs[idx]:
        show_programs_summary(result, configs, computed["intra_results"], computed["cross_results"])
    idx += 1
    with tabs[idx]:
        if code:
            show_applicant_profile(computed, code, campaign_year, scenario, window)
        else:
            st.info("Введите уникальный код в боковой панели.")
    idx += 1
    with tabs[idx]:
        show_program_ranking(computed, scenario, window, code)
    idx += 1
    if cross_key == "probabilistic":
        with tabs[idx]:
            show_probabilistic(computed, scenario, weights)
        idx += 1
    with tabs[idx]:
        show_warnings(warnings)
    idx += 1
    with tabs[idx]:
        show_exports(computed, warnings, scenario, code, campaign_year)


if __name__ == "__main__":
    main()
