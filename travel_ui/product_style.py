"""Static presentation styles for the local product demo."""

import streamlit as st


def render_product_landing() -> None:
    """Make the purpose and next action visible without relying on images."""
    st.markdown("""
<style>
.stApp { background: #f7f9f8; }
.block-container { max-width: 1280px; padding-top: 2.4rem; }
[data-testid="stSidebar"] { background: #edf3f0; }
h1, h2, h3 { color: #193b35; letter-spacing: -.025em; }
.travel-hero { padding: 2rem 2.2rem; border: 1px solid #dce8e1;
  border-radius: 20px; background: linear-gradient(115deg, #edf5f0, #fffdf6);
  margin-bottom: 1.4rem; }
.travel-eyebrow { color: #496b5c; text-transform: uppercase; font-size: .76rem;
  letter-spacing: .15em; font-weight: 700; margin-bottom: .8rem; }
.travel-hero h1 { font-size: clamp(2rem, 4vw, 3.15rem); line-height: 1.25;
  max-width: 850px; margin: 0 0 1rem; padding: 0; }
.travel-hero p { color: #486059; line-height: 1.8; max-width: 820px; margin: 0; }
.travel-steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem;
  margin: 1.3rem 0 1.6rem; }
.travel-step { background: white; border: 1px solid #e0e8e3; border-radius: 12px;
  padding: 1rem 1.2rem; color: #294c41; }
.travel-step b { display: block; margin-bottom: .3rem; }
.travel-step span { font-size: .87rem; color: #64746c; }
[data-testid="stMetricValue"] { color: #1d5742; }
@media(max-width: 700px) {
  .travel-steps { grid-template-columns: 1fr; gap: .6rem; }
  .travel-hero { padding: 1.5rem; }
}
</style>
<section class="travel-hero" aria-label="随心航线产品介绍">
<div class="travel-eyebrow">随心航线 / A journey worth comparing</div>
<h1>先有旅行的念头，<br>再发现值得去的组合。</h1>
<p>目的地还没想好？说说你的时间、预算和偏好。我们一起探索多站旅行，
比较哪条更省机票、哪条少折腾，以及每一种选择的代价。</p>
</section>
<div class="travel-steps">
<div class="travel-step"><b>01 · 描述想法</b><span>一句话开始，也可以手动填写或选择示例。</span></div>
<div class="travel-step"><b>02 · 确认条件</b><span>检查日期、预算和必去地点，修正不确定信息。</span></div>
<div class="travel-step"><b>03 · 比较取舍</b><span>同时看价格、交通和体验，选择值得继续核对的路线。</span></div>
</div>
""", unsafe_allow_html=True)
    st.caption("AI-assisted multi-objective travel decision prototype · AI 理解意图，确定性引擎生成路线。")
