import streamlit as st
import io
import sys
from agent import query_agent
from ingestion import ingest_txt_screenplay
from gemini_reliability import GeminiQuotaError

if "active_production_id" not in st.session_state:
    st.session_state["active_production_id"] = "demo_sample"

# Page configuration
st.set_page_config(
    page_title="SceneKeeper | AI Continuity Agent",
    page_icon="🎬",
    layout="wide"
)

# Custom Styling
st.markdown("""
    <style>
    .stApp { max-width: 1200px; margin: 0 auto; }
    .main-title { font-size: 2.2rem; font-weight: 700; color: #1E293B; margin-bottom: 0.2rem; }
    .sub-title { font-size: 1.0rem; color: #64748B; margin-bottom: 1.5rem; }
    .badge { background-color: #E2E8F0; padding: 4px 8px; border-radius: 4px; font-weight: 600; font-size: 0.8rem; }
    </style>
""", unsafe_allow_html=True)

# Header
st.markdown('<div class="main-title">🎬 SceneKeeper</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-title">AI Script Supervisor & Continuity Memory Agent for Film Productions</div>',
    unsafe_allow_html=True
)

# Sidebar
with st.sidebar:
    st.header("System Status")
    st.markdown(f"**Active Production:** `{st.session_state['active_production_id']}`")
    st.success("🟢 ClickHouse Cloud Connected")
    st.success("🟢 Official mcp-clickhouse Server Active")
    st.success("🟢 Gemini 3.6 Flash Active")
    st.divider()

    st.markdown("**Architecture Highlights:**")
    st.markdown("- **MCP Protocol:** stdio subprocess bridge")
    st.markdown("- **DB Engine:** ClickHouse Cloud")
    st.markdown("- **LLM Engine:** Gemini 3.6 Flash")

# Main Interface Tabs
tab1, tab2, tab3 = st.tabs(["🔍 Continuity Audit", "📋 Production Tables", "📥 Import Production"])

with tab1:
    st.subheader("Run Script Supervisor Audit")

    default_prompt = (
    "Audit the entire active production for continuity problems across all scenes. "
    "Compare character wardrobe, physical condition, knowledge state, props, "
    "prop ownership, prop condition, and prop location between scenes. "
    "Identify unexplained changes, explain why each may be a continuity issue, "
    "and also mention important details that remain consistent."
)

prompt_option = st.radio(
    "Choose audit mode:",
    [
        "Audit Entire Production",
        "Custom Prompt"
    ]
)

if prompt_option == "Custom Prompt":
    user_prompt = st.text_area(
        "Ask SceneKeeper:",
        value="",
        placeholder="Example: Track Mara's wardrobe and physical condition across all scenes.",
        height=100
    )
else:
    user_prompt = default_prompt

    if st.button("Run Continuity Check", type="primary"):
        with st.spinner(
            "Invoking mcp-clickhouse and evaluating continuity via Gemini..."
        ):
            old_stdout = sys.stdout
            sys.stdout = buffer = io.StringIO()
            try:
                report = query_agent(
                    user_prompt,
                    active_production_id=st.session_state.get(
                        "active_production_id",
                        "demo_sample"
                    )
                )

                sys.stdout = old_stdout
                console_logs = buffer.getvalue()

                st.markdown("### 📄 Continuity Report")
                st.markdown(report)

                with st.expander("🛠️ View Live MCP Execution Logs"):
                    st.code(console_logs, language="text")

            except GeminiQuotaError as e:
                sys.stdout = old_stdout
                st.warning(str(e))
            except Exception as e:
                sys.stdout = old_stdout
                st.error(f"Error during execution: {str(e)}")
with tab2:
    st.subheader("Direct Database Context")
    st.info(
        "SceneKeeper queries these tables via the "
        "`mcp-clickhouse` server at runtime."
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("**`scenekeeper.scenes`**")
        st.caption(
            "Tracks scene metadata, location, and script summaries."
        )

    with col2:
        st.markdown("**`scenekeeper.character_states`**")
        st.caption(
            "Tracks wardrobe, physical condition, and character knowledge."
        )

    with col3:
        st.markdown("**`scenekeeper.prop_tracker`**")
        st.caption(
            "Tracks prop locations, conditions, and character handoffs."
        )

with tab3:
    st.subheader("Import Screenplay (.txt)")
    st.caption(
        "Upload a screenplay from your computer. SceneKeeper will use Gemini "
        "to extract scenes, character states, and props into ClickHouse."
    )

    uploaded_file = st.file_uploader(
        "Choose a TXT screenplay",
        type=["txt"]
    )

    if uploaded_file is not None:
        if st.button("Process & Import Screenplay", type="primary"):
            with st.spinner(
                "Gemini is analyzing the screenplay and writing continuity data..."
            ):
                try:
                    script_text = uploaded_file.read().decode("utf-8")
                    result = ingest_txt_screenplay(script_text)

                    st.session_state["active_production_id"] = result["production_id"]

                    st.success(f"Successfully imported **{result['title']}**!")

                    st.json({
                        "Production ID": result["production_id"],
                        "Scenes Extracted": result["scene_count"],
                        "Character States Extracted": result["character_count"],
                        "Props Tracked": result["prop_count"]
                    })

                    st.rerun()

                except GeminiQuotaError as e:
                    st.warning(str(e))
                except Exception as e:
                    st.error(f"Ingestion Failed: {e}")
