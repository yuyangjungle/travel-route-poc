"""Streamlit product journey: understand, confirm, and compare travel intent."""

from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import time

import streamlit as st

from benchmark_tools.validation import ValidationError, load_dataset, validate_request
from travel_core import search
from travel_data import SyntheticDestinationProvider
from travel_ui.demo_cases import case_by_id, load_demo_cases
from travel_ui.natural_language import OpenAITravelRequestExtractor
from travel_ui.pilot import (build_pilot_record, build_product_telemetry,
                             build_subjective_feedback, create_session_id,
                             pilot_record_json)
from travel_ui.presentation import build_itinerary_cards, comparison_rows, display_name
from travel_ui.product_style import render_product_landing
from travel_ui.scenario_library import (load_destination_descriptions,
                                        load_m6_scenarios)
from travel_ui.view_models import PREFERENCE_NAMES, build_request, result_message

ROOT = Path(__file__).resolve().parent
DEMO_DIR = ROOT / "demos"
M6_SCENARIO_DIR = ROOT / "scenarios" / "m6"
DESCRIPTION_FILE = ROOT / "data" / "m6" / "destination_descriptions.json"


@st.cache_resource
def get_dataset(relative_path: str):
    return load_dataset(ROOT / relative_path)


@st.cache_data
def get_all_cases():
    legacy = load_demo_cases(DEMO_DIR)
    for case in legacy:
        case.update(
            scenario_kind="benchmark", dataset_path="data/m1",
            traveler_profile="技术基准演示", expected_user_intent=case["description"],
            decision_priority="验证固定产品假设",
            must_have_constraints=["使用 M1 固定报价子集"],
        )
    return load_m6_scenarios(M6_SCENARIO_DIR, ROOT) + legacy


@st.cache_data
def get_destination_descriptions():
    return load_destination_descriptions(DESCRIPTION_FILE)


@st.cache_data
def get_destination_profiles(dataset_path: str) -> dict:
    if dataset_path != "data/m5":
        return {}
    provider = SyntheticDestinationProvider(get_dataset(dataset_path))
    return {profile.id: profile.to_dict() for profile in provider.list_destinations()}


def render_card(card: dict, profiles: dict | None = None) -> None:
    with st.container(border=True):
        st.markdown(f"### {card['rank']}. {card['label']}")
        st.markdown(f"**{card['route']}**")
        destinations = " · ".join(
            f"{visit['destination']} {visit['stay_nights']} 晚" for visit in card["destinations"]
        )
        st.caption(f"停留：{destinations} ｜ 全程 {card['trip_days']} 天 ｜ {card['flight_days']} 个飞行日")
        columns = st.columns(4)
        columns[0].metric("预计机票", card["flight_cost"])
        columns[1].metric("交通总时长", card["transit"])
        columns[2].metric("交通负担", card["burden"])
        columns[3].metric("偏好 / 停留", card["experience"])
        st.progress(min(1.0, float(card["overall"]) / 100), text=f"综合分 {card['overall']} / 100")
        left, right = st.columns(2)
        with left:
            st.markdown("**Why recommended｜为什么推荐**")
            for reason in card["why"]:
                st.markdown(f"- {reason}")
        with right:
            st.markdown("**Trade-offs｜主要取舍**")
            for tradeoff in card["tradeoffs"]:
                st.markdown(f"- {tradeoff}")
        with st.expander("查看航班与停留细节"):
            st.dataframe(card["legs"], hide_index=True, use_container_width=True)
            for visit in card["destinations"]:
                st.markdown(
                    f"**{visit['destination']}** · {visit['stay_nights']} 晚 · "
                    f"该站体验收益 {float(visit['experience_points']):.1f}"
                )
                st.caption(f"抵达目的地 {visit['arrival_at']} ｜ 离开目的地 {visit['departure_at']}")
                optimization = visit["optimization_data"]
                descriptive = visit["descriptive_information"]
                st.markdown(
                    f"优化数据：建议停留 **{optimization['recommended_stay_nights']} 晚** · "
                    f"当月季节分 **{optimization['season_score']}/100** · "
                    f"往返机场接驳 **{optimization['airport_transfer_minutes']} 分钟**"
                )
                st.markdown(
                    f"展示信息（模拟）：**{descriptive['destination_type']}** · "
                    f"适合 {' / '.join(descriptive['suitable_activities']) or '暂未标注'} · "
                    f"交通难度 {descriptive['transport_difficulty']}"
                )
                st.caption(f"{descriptive['seasonal_note']} {descriptive['transport_note']}")
                profile = (profiles or {}).get(visit["destination_id"])
                if profile:
                    knowledge = profile["descriptive"]
                    provenance = knowledge["provenance"]
                    months = "、".join(str(month) for month in knowledge["best_season_months"])
                    st.markdown(f"**目的地档案（模拟）** · 建议体验月份：{months} 月")
                    st.caption(
                        f"{knowledge['seasonal_note']} · 更新时间 {provenance['updated_at'][:10]} · "
                        "可信度：模拟、未经真实资料核验。季节描述用于理解目的地，不参与优化。"
                    )


def render_pilot_feedback(last_run: dict, cards: list[dict]) -> None:
    st.divider()
    st.subheader("本次测试反馈")
    st.caption(
        "用于小范围产品验证；不填写姓名、邮箱或联系方式。记录只在当前会话中生成，"
        "不会自动上传，请下载后交给测试组织者。"
    )
    option_labels = {None: "尚未选择"}
    option_labels.update({
        card["itinerary_id"]: f"方案 {card['rank']} · {card['route']}"
        for card in cards
    })
    response_labels = {"yes": "是", "no": "否", "unsure": "不确定"}
    response_options = ["unsure", "yes", "no"]
    with st.form("pilot-feedback"):
        selected = st.selectbox(
            "如果现在必须继续规划，你会优先核对哪条路线？",
            options=list(option_labels), format_func=option_labels.get,
            disabled=not cards,
        )
        columns = st.columns(3)
        with columns[0]:
            comprehension = st.select_slider(
                "我能解释推荐类别的差别", options=[1, 2, 3, 4, 5], value=3,
                help="1 = 完全不能，5 = 完全能",
            )
        with columns[1]:
            trust = st.select_slider(
                "现有信息足以让我决定是否继续核对", options=[1, 2, 3, 4, 5], value=3,
                help="1 = 完全不同意，5 = 完全同意",
            )
        with columns[2]:
            usefulness = st.select_slider(
                "这些方案帮助了我的下一步规划", options=[1, 2, 3, 4, 5], value=3,
                help="1 = 完全不同意，5 = 完全同意",
            )
        discovery = st.selectbox(
            "是否出现了你原本没有想到的路线组合？",
            options=response_options, format_func=response_labels.get,
        )
        consideration = st.selectbox(
            "你会认真考虑刚才选择的路线吗？",
            options=response_options, format_func=response_labels.get,
            disabled=not cards,
        )
        unclear = st.text_area("哪一处最难理解？（可留空）", max_chars=500)
        missing = st.text_area("做真实决定前，你还需要核对什么？（可留空）", max_chars=500)
        submitted = st.form_submit_button("生成测试记录", use_container_width=True)

    if submitted:
        telemetry = build_product_telemetry(
            session_id=last_run["session_id"], scenario_id=last_run["case_id"],
            result=last_run["result"], cards=cards,
            completion_seconds=time.monotonic() - last_run["task_started"],
        )
        feedback = build_subjective_feedback(
            selected_itinerary_id=selected,
            comprehension_rating=comprehension, trust_rating=trust,
            usefulness_rating=usefulness,
            discovered_non_obvious_route=discovery,
            would_consider_selected_route=consideration if cards else "unsure",
            unclear_point=unclear, missing_information=missing,
        )
        st.session_state["pilot_record"] = build_pilot_record(telemetry, feedback)
        st.session_state["pilot_record_case"] = last_run["case_id"]

    record = st.session_state.get("pilot_record")
    if record and st.session_state.get("pilot_record_case") == last_run["case_id"]:
        st.success("测试记录已生成。请下载 JSON 并交给测试组织者。")
        st.download_button(
            "下载测试记录（JSON）", data=pilot_record_json(record),
            file_name=f"{last_run['session_id']}-{last_run['case_id']}.json",
            mime="application/json", use_container_width=True,
        )


def store_run(case_id: str, request: dict, result: dict) -> None:
    st.session_state.pop("pilot_record", None)
    st.session_state.pop("pilot_record_case", None)
    st.session_state["last_run"] = {
        "case_id": case_id, "request": request, "result": result,
        "session_id": st.session_state["pilot_session_id"],
        "task_started": st.session_state["pilot_task_started"],
    }


def render_results(selected_id: str, data, descriptions: dict, profiles: dict | None = None) -> None:
    last_run = st.session_state.get("last_run")
    if not last_run:
        st.caption("提交旅行条件后，系统会返回多个可比较方案。")
        return
    if last_run["case_id"] != selected_id:
        st.caption("当前模式尚未运行，请先提交旅行条件。")
        return
    result = last_run["result"]
    level, message = result_message(result)
    getattr(st, level)(message)
    cards = []
    if result["status"] != "ok":
        with st.expander("为什么可能没有结果？", expanded=True):
            st.write(
                "这表示在本场景加载的模拟报价中，没有路线能同时满足当前日期、行程天数、"
                "预算、必去地点和中转限制；不表示现实市场一定没有航班。"
            )
            st.write("可以一次只调整一个条件后重试。当前数据不足以可靠判断是哪一项单独造成无解。")
        if result["diagnostics"].get("rejected_extensions"):
            with st.expander("查看约束诊断"):
                st.write(result["diagnostics"]["rejected_extensions"])
        st.caption("系统没有自动放宽预算、日期、必去地点或连接限制。")
    else:
        st.subheader("发现并比较可行组合")
        st.markdown(
            "下面是多目标 Pareto 前沿中的代表方案。它们分别突出价格、交通负担或体验，"
            "系统不会把其中一条包装成唯一正确答案。"
        )
        with st.expander("推荐类别和分数是什么意思？"):
            st.markdown(
                "- **最便宜**：当前约束与模拟报价中的最低机票总价。\n"
                "- **最少折腾**：交通时长、飞行日、中转和夜航组成的负担分最低。\n"
                "- **偏好最匹配**：偏好标签与停留时间的体验收益最高。\n"
                "- **综合推荐**：按固定 score-v1 权重平衡费用、负担和体验；不是满意概率。\n"
                "- **Pareto 前沿**：想继续改善一个目标，就必须牺牲至少另一个目标。"
            )
        st.caption(
            f"展示 {len(result['itineraries'])} 个代表方案 · 数据 {result['dataset_version']} · "
            f"评分 {result['scoring_version']} · 搜索 {result['search_config']['search_version']}"
        )
        cards = build_itinerary_cards(result["itineraries"], data, last_run["request"], descriptions)
        st.dataframe(comparison_rows(cards, descriptions), hide_index=True, use_container_width=True)
        st.caption("差值只比较本次同约束、同模拟报价下的代表方案；用于支持决策，不替用户做唯一选择。")
        st.subheader("逐项理解推荐")
        for card in cards:
            render_card(card, profiles)
        st.caption("“最优”只针对本场景加载的模拟报价和当前离散规则，不代表真实市场的全部航班。")
    render_pilot_feedback(last_run, cards)


def secret_or_environment(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name)
    except (FileNotFoundError, KeyError):
        value = None
    return str(value or os.environ.get(name, default)).strip()


INTENT_EXAMPLES = {
    "海岛与潜水": "2027 年 10 月 1 日到 21 日之间，从上海浦东或杭州出发并返回，玩 10–14 天。必须去仙本那，喜欢潜水、海岛和美食，单人机票预算 8000 元，每程最多中转 1 次，不接受自行转机。",
    "毕业旅行": "2027 年 10 月 1 日到 21 日，从杭州出发并返回，想玩 8–12 天，单人机票 4500 元以内。喜欢海滩和街头美食，目的地开放，最多接受 1 次中转，不接受自行转机。",
    "长假探索": "2027 年 10 月 1 日到 21 日，从上海浦东出发，也可以回杭州，玩 12–18 天，单人机票预算 9000 元。想体验文化、美食和自然，愿意增加两三个地方，每程最多中转 1 次。",
    "轻松度假": "2027 年 10 月 1 日到 21 日，从上海浦东出发并返回，休假 7–10 天，单人机票预算 15000 元，海岛放松优先，希望直飞，最多增加 1 个目的地。",
}


def invalidate_discovery() -> None:
    """A changed source must not confirm or display a previous interpretation."""
    for key in ("ai_extraction", "ai_source_text", "last_run", "pilot_record", "pilot_record_case"):
        st.session_state.pop(key, None)


def select_intent_example() -> None:
    st.session_state["ai_text"] = INTENT_EXAMPLES[st.session_state["ai_example"]]
    invalidate_discovery()


def extract_product_intent(text: str, data, descriptions: dict, *, api_key: str,
                           provider: str, model: str) -> dict:
    """Use the M9 draft contract while retaining the frozen legacy adapter."""
    if provider == "deepseek":
        from travel_product.intent import extract_intent
        return extract_intent(text, data, descriptions, api_key=api_key, model=model)
    legacy = OpenAITravelRequestExtractor(api_key=api_key, model=model).extract(
        text, data, descriptions, request_id="ui-ai-discovery"
    )
    fields = {key: value for key, value in legacy.request.items() if key in {
        "origin_airport_ids", "return_airport_ids", "window_start_date", "window_end_date",
        "min_trip_days", "max_trip_days", "required_destination_ids", "preferred_destination_ids",
        "preference_weights", "max_optional_destinations", "max_connections_per_offer", "allow_self_transfer",
    }}
    fields["flight_budget_cny"] = legacy.request["flight_budget_minor"] // 100
    return {"contract_version": "legacy-v1", "fields": fields, "summary": legacy.summary,
            "assumptions": legacy.assumptions, "unresolved_mentions": legacy.unresolved_mentions,
            "missing_fields": [], "model": legacy.model}


def render_intent_confirmation(extraction: dict, case: dict, data, descriptions: dict) -> None:
    st.subheader("02 · 检查并修正旅行条件")
    st.write(extraction["summary"])
    st.caption("以下是 AI 对文字的理解。每一项都可以修改，空白项需要补全；勾选确认后才会搜索。")
    if extraction["assumptions"]:
        st.info("需要你确认的假设：" + "；".join(extraction["assumptions"]))
    if extraction["unresolved_mentions"]:
        st.warning("仍不确定或未覆盖：" + "；".join(extraction["unresolved_mentions"]))
    if extraction.get("missing_fields"):
        st.warning("描述中还有缺失信息，请补全下方空白字段后继续。")

    fields = extraction["fields"]
    revision = st.session_state.get("ai_revision", 0)
    prefix = f"ai-confirm-{revision}"
    home_airports = [item for item in ("PVG", "HGH", "NKG") if item in data.airports]
    destination_ids = sorted(data.destinations, key=lambda item: display_name(item, descriptions))
    with st.form("ai-confirmation"):
        left, right = st.columns(2)
        with left:
            origins = st.multiselect("可选出发机场", home_airports,
                                     default=fields["origin_airport_ids"], key=f"{prefix}-origins")
            start = st.date_input("最早出发日期", value=date.fromisoformat(fields["window_start_date"])
                                  if fields["window_start_date"] else None, key=f"{prefix}-start")
            min_days = st.number_input("最短旅行天数", min_value=7, max_value=21,
                                       value=fields["min_trip_days"], step=1, key=f"{prefix}-min-days")
            required = st.multiselect("必去目的地", destination_ids,
                                      default=fields["required_destination_ids"],
                                      format_func=lambda item: display_name(item, descriptions),
                                      key=f"{prefix}-required")
        with right:
            returns = st.multiselect("允许返程机场", home_airports,
                                     default=fields["return_airport_ids"], key=f"{prefix}-returns")
            end = st.date_input("最晚返回日期", value=date.fromisoformat(fields["window_end_date"])
                                if fields["window_end_date"] else None, key=f"{prefix}-end")
            max_days = st.number_input("最长旅行天数", min_value=7, max_value=21,
                                       value=fields["max_trip_days"], step=1, key=f"{prefix}-max-days")
            preferred = st.multiselect("想去目的地（非必去）", destination_ids,
                                       default=fields["preferred_destination_ids"],
                                       format_func=lambda item: display_name(item, descriptions),
                                       key=f"{prefix}-preferred")
        budget = st.number_input("单人机票预算（元）", min_value=1,
                                  value=fields["flight_budget_cny"], step=100, key=f"{prefix}-budget")
        st.markdown("**旅行偏好** · 0 表示不影响排序，100 表示非常在意")
        columns = st.columns(len(data.manifest["preference_tag_ids"]))
        weights = {}
        for column, tag in zip(columns, data.manifest["preference_tag_ids"]):
            with column:
                weights[tag] = st.slider(PREFERENCE_NAMES.get(tag, tag), 0, 100,
                                         value=fields["preference_weights"].get(tag, 0),
                                         key=f"{prefix}-weight-{tag}")
        left, right = st.columns(2)
        with left:
            optional_limit = st.slider("最多增加几个可选目的地", 0, 4,
                                        value=fields["max_optional_destinations"], key=f"{prefix}-optional")
        with right:
            connections = st.number_input("每张单程报价最多中转次数", min_value=0, max_value=10,
                                           value=fields["max_connections_per_offer"], step=1,
                                           key=f"{prefix}-connections")
        self_transfer = st.checkbox("允许自行转机（当前数据不支持；需取消后继续）",
                                     value=fields.get("allow_self_transfer", False), key=f"{prefix}-self-transfer")
        acknowledged = st.checkbox("我已逐项核对以上条件和假设，并修正了缺失或不确定信息。",
                                   key=f"{prefix}-acknowledged")
        resolved = True
        if extraction["unresolved_mentions"]:
            resolved = st.checkbox("我已明确处理上述未覆盖内容；未在表单选中的条件不再作为本次搜索要求。",
                                    key=f"{prefix}-resolved")
        confirm = st.form_submit_button("确认条件并生成多个方案", type="primary", use_container_width=True)

    if not confirm:
        return
    st.session_state.pop("last_run", None)
    if not acknowledged or not resolved:
        st.error("请先核对条件，并勾选确认。存在不确定内容时，还需要明确处理后再搜索。")
        return
    if not origins or not returns or None in (start, end, min_days, max_days, budget):
        st.error("请补全出发机场、返程机场、两个日期、旅行天数和机票预算。")
        return
    try:
        request = build_request(
            request_id="ui-ai-discovery", origins=origins, returns=returns,
            date_window=(start, end), duration=(min_days, max_days), budget_cny=budget,
            required_destinations=required, preferred_destinations=preferred, preference_weights=weights,
            max_optional_destinations=optional_limit, max_connections_per_offer=connections,
            fare_profile_id=data.manifest["fare_profile_id"],
        )
        request["allow_self_transfer"] = self_transfer
        validate_request(request, data)
    except (ValidationError, ValueError):
        st.error("条件仍有冲突或超出覆盖范围。请检查日期先后、天数范围、必去与想去重叠，以及自行转机选项。")
        st.caption(f"模拟数据日期范围：{data.manifest['coverage_start_date']} 至 {data.manifest['coverage_end_date']}。")
        return
    with st.spinner("正在比较日期、目的地组合与交通代价…"):
        store_run("ai-discovery", request, search(request, data, case["offer_ids"]))


def render_discovery_mode(case: dict, data, descriptions: dict) -> None:
    st.subheader("01 · 用一句话描述旅行想法")
    st.caption("AI 只把文字转换为结构化条件，不生成路线，也看不到航班报价。你确认后，确定性优化器才会搜索多个方案。")
    st.selectbox("从一个想法开始", list(INTENT_EXAMPLES), key="ai_example", on_change=select_intent_example)
    if "ai_text" not in st.session_state:
        st.session_state["ai_text"] = INTENT_EXAMPLES["海岛与潜水"]
    natural_text = st.text_area("旅行描述", key="ai_text", height=130, max_chars=2000,
                                help="写下出发地、日期范围、时长、机票预算、必去地点、偏好和中转容忍度。")
    if st.session_state.get("ai_source_text") not in (None, natural_text):
        invalidate_discovery()
        st.info("旅行描述已更新，请重新提取条件；之前的理解与路线已清除。")

    api_key = secret_or_environment("DEEPSEEK_API_KEY")
    provider = "deepseek" if api_key else "openai"
    if provider == "deepseek":
        model = secret_or_environment("DEEPSEEK_MODEL", "deepseek-flash")
    else:
        api_key = secret_or_environment("OPENAI_API_KEY")
        model = secret_or_environment("OPENAI_MODEL", "gpt-5-mini")
    if not api_key:
        st.warning("AI 输入尚未配置。请在服务端设置 DEEPSEEK_API_KEY（或 OPENAI_API_KEY），也可以切换到“手动规划模式”直接体验。")
    parse_clicked = st.button("AI 提取旅行条件", type="primary", use_container_width=True,
                              disabled=not bool(api_key), key="ai-parse")
    if parse_clicked:
        invalidate_discovery()
        try:
            with st.spinner("正在理解你的旅行条件…"):
                extraction = extract_product_intent(natural_text, data, descriptions,
                                                      api_key=api_key, provider=provider, model=model)
            st.session_state["ai_extraction"] = extraction
            st.session_state["ai_source_text"] = natural_text
            st.session_state["ai_revision"] = st.session_state.get("ai_revision", 0) + 1
        except Exception:
            # The provider may include credentials/request headers in an exception.
            # Neither raw errors nor provider responses belong in the interface.
            st.error("暂时无法提取旅行条件。请稍后重试，或使用手动规划模式填写；你的条件尚未用于搜索。")

    extraction = st.session_state.get("ai_extraction")
    if extraction:
        render_intent_confirmation(extraction, case, data, descriptions)
    render_results("ai-discovery", data, descriptions, get_destination_profiles(case["dataset_path"]))


def main() -> None:
    st.set_page_config(page_title="随心航线 · Travel Decision Prototype", page_icon="🧭", layout="wide")
    render_product_landing()
    st.info("这是使用固定模拟报价的概念验证。价格不可用于购票，评分也不是满意概率。", icon="ℹ️")
    cases = get_all_cases()
    descriptions = get_destination_descriptions()
    product_mode = st.sidebar.radio(
        "规划方式", options=["发现模式", "手动规划模式"],
        help="发现模式用 AI 理解文字；两种模式最终都调用同一个确定性优化器。",
    )
    if product_mode == "发现模式":
        selected_id = "ai-discovery"
        case = case_by_id(cases, "m6-long-holiday-exploration")
        case.update(
            id=selected_id, title="AI 发现模式",
            traveler_profile="目的地尚未完全确定，希望从自然语言开始探索的旅客",
            expected_user_intent="先提取约束，再比较多个非支配组合；不生成唯一答案。",
            decision_priority="发现意外组合，同时看清价格、体验与交通代价",
            must_have_constraints=["AI 只能使用当前目录", "搜索和评分仍由确定性优化器执行"],
        )
    else:
        ids = ["custom"] + [item["id"] for item in cases]
        names = {"custom": "自定义探索（M1 全部模拟报价）", **{item["id"]: item["title"] for item in cases}}
        selected_id = st.sidebar.selectbox(
            "演示场景", options=ids, index=ids.index("m6-island-diving-trip"),
            format_func=lambda value: names[value],
            help="切换场景会载入一组固定、可复现的模拟报价。",
        )
        if selected_id == "custom":
            case = case_by_id(cases, "extra-destination-cheaper")
            case.update(
                id="custom", title=names["custom"],
                description="使用全部 15 条模拟报价，自由调整条件。数据仍只覆盖 2026 年 10 月。",
                source_scenario="full M1 synthetic dataset", offer_ids=None,
                dataset_path="data/m1", scenario_kind="custom",
            )
        else:
            case = case_by_id(cases, selected_id)
    data = get_dataset(case["dataset_path"])
    if "pilot_session_id" not in st.session_state:
        st.session_state["pilot_session_id"] = create_session_id()
    if st.session_state.get("pilot_active_case") != selected_id:
        st.session_state["pilot_active_case"] = selected_id
        st.session_state["pilot_task_started"] = time.monotonic()
        st.session_state.pop("pilot_record", None)
        st.session_state.pop("pilot_record_case", None)
    default = case["request"]
    st.sidebar.markdown("**旅客与决策意图**")
    st.sidebar.write(case["traveler_profile"])
    st.sidebar.caption(case["expected_user_intent"])
    st.sidebar.markdown("**决策重点**")
    st.sidebar.write(case["decision_priority"])
    with st.sidebar.expander("必须满足的条件"):
        for constraint in case["must_have_constraints"]:
            st.markdown(f"- {constraint}")
    st.sidebar.caption(f"固定报价子集 · 来源 {case['source_scenario']}")
    home_airports = [airport for airport in ("PVG", "HGH", "NKG") if airport in data.airports]
    destination_ids = sorted(data.destinations, key=lambda item: display_name(item, descriptions))
    widget = selected_id

    if product_mode == "发现模式":
        render_discovery_mode(case, data, descriptions)
        return

    with st.form("optimizer-inputs"):
        st.subheader("旅行条件")
        st.caption("日期窗口限制出发与返回日期；总行程天数是实际旅行长度；预算只包含一名成人的模拟机票。")
        first, second, third = st.columns(3)
        with first:
            origins = st.multiselect(
                "可选出发机场", home_airports,
                default=[item for item in default["origin_airport_ids"] if item in home_airports],
                key=f"origin-{widget}",
            )
            returns = st.multiselect(
                "允许返程机场", home_airports,
                default=[item for item in default["return_airport_ids"] if item in home_airports],
                key=f"returns-{widget}",
                help="允许从不同机场回国，例如 PVG 出发、HGH 返回。",
            )
        with second:
            date_window = st.date_input(
                "旅行日期窗口",
                value=(date.fromisoformat(default["window_start_date"]),
                       date.fromisoformat(default["window_end_date"])),
                min_value=date.fromisoformat(data.manifest["coverage_start_date"]),
                max_value=date.fromisoformat(data.manifest["coverage_end_date"]),
                key=f"dates-{widget}",
                help="首段起飞和末段抵达都必须落在窗口内。",
            )
            duration = st.slider(
                "总行程天数", 7, 21,
                value=(default["min_trip_days"], default["max_trip_days"]),
                key=f"duration-{widget}",
                help="优化器会在这个最短与最长天数之间寻找可行组合。",
            )
        with third:
            budget = st.number_input(
                "单人机票预算（元）", min_value=1, max_value=50000,
                value=default["flight_budget_minor"] // 100, step=100,
                key=f"budget-{widget}",
            )
            required = st.multiselect(
                "必去目的地", destination_ids,
                default=default["required_destination_ids"],
                format_func=lambda item: display_name(item, descriptions), key=f"required-{widget}",
                help="机场中转不会被算作到访。",
            )
            preferred = st.multiselect(
                "想去目的地（非必去）", [item for item in destination_ids if item not in required],
                default=[item for item in default["preferred_destination_ids"] if item not in required],
                format_func=lambda item: display_name(item, descriptions), key=f"preferred-{widget}",
                help="选择后会获得固定偏好加分，但不保证一定加入路线。",
            )
        st.subheader("旅行偏好")
        st.caption("0 表示不影响排序，100 表示非常在意；偏好只影响评分，不会绕过预算或必去地点。")
        preference_columns = st.columns(len(data.manifest["preference_tag_ids"]))
        weights = {}
        for column, tag in zip(preference_columns, data.manifest["preference_tag_ids"]):
            with column:
                weights[tag] = st.slider(
                    PREFERENCE_NAMES.get(tag, tag), 0, 100,
                    value=default["preference_weights"].get(tag, 0),
                    key=f"preference-{tag}-{widget}",
                )
        with st.expander("更多约束"):
            optional_limit = st.slider(
                "最多增加几个可选目的地", 0, 4,
                value=default["max_optional_destinations"], key=f"optional-{widget}",
            )
            connections = st.slider(
                "每张单程报价最多中转次数", 0, 2,
                value=default["max_connections_per_offer"], key=f"connections-{widget}",
            )
        submitted = st.form_submit_button("开始优化", type="primary", use_container_width=True)

    if submitted:
        if not isinstance(date_window, (tuple, list)) or len(date_window) != 2:
            st.error("请选择开始和结束两个日期。")
            return
        if not origins or not returns:
            st.error("至少选择一个出发机场和一个允许返程机场。")
            return
        request = build_request(
            request_id=f"ui-{selected_id}", origins=origins, returns=returns,
            date_window=(date_window[0], date_window[1]), duration=duration,
            budget_cny=budget, required_destinations=required,
            preferred_destinations=preferred,
            preference_weights=weights, max_optional_destinations=optional_limit,
            max_connections_per_offer=connections,
            fare_profile_id=data.manifest["fare_profile_id"],
        )
        try:
            validate_request(request, data)
        except ValidationError:
            st.session_state.pop("last_run", None)
            st.error("条件存在冲突。请核对日期、天数，以及必去和想去目的地是否重叠。")
            return
        with st.spinner("正在比较日期、目的地组合与交通代价…"):
            store_run(selected_id, request, search(request, data, case["offer_ids"]))

    render_results(selected_id, data, descriptions, get_destination_profiles(case["dataset_path"]))


if __name__ == "__main__":
    main()
