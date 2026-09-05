import streamlit as st

st.title("Trail Route Generation (dev)")

st.write("Development/debugging interface for trail route generation.")

st.sidebar.header("Route")

races = [
    "Select a race...",
    "Istria 168K",
    "Istria 110K",
    "Istria 69K",
    "JAT 120K",
    "JAT 80K",
    "JAT 55K",
    "Hochkönig HK70",
    "IATF K25",
]

selected_race = st.sidebar.selectbox(
    "Race",
    races,
    index=0,
    help="Select a race to inspect or use as the basis for route generation.",
)

if selected_race != "Select a race...":
    st.success(f"Selected race: **{selected_race}**")