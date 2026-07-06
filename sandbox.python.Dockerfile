FROM python:3.13-trixie

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=

ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    MPLBACKEND=Agg

# Pango/HarfBuzz: WeasyPrint runtime deps. Fonts: the base image only ships
# 8 DejaVu faces (no italic, no Arial/Times metric equivalents) — too little
# for HTML/PDF rendering.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
        fonts-dejavu fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    # data & math
    numpy pandas polars pyarrow duckdb scipy sympy statsmodels scikit-learn \
    # plotting
    matplotlib seaborn plotly \
    # images & vision
    pillow imageio opencv-python-headless \
    # office / document formats
    openpyxl xlsxwriter xlrd python-docx python-pptx \
    pypdf pdfplumber pymupdf reportlab weasyprint fpdf2 \
    # web & parsing
    requests httpx beautifulsoup4 lxml html5lib \
    # misc
    pyyaml tabulate markdown jinja2 networkx tqdm rapidfuzz qrcode wordcloud

# Build matplotlib's font cache now so the first plot in a sandbox is fast.
RUN python -c "import matplotlib.pyplot"

WORKDIR /sandbox
