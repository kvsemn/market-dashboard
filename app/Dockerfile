FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
COPY assortment_view.py .
COPY event_navigation.py .
COPY event_context.py .
COPY shared_filters.py .
COPY price_model.py .
COPY market_view.py .
COPY event_details.py .
COPY sales_view.py .
COPY history_view.py .
COPY map_view.py .
COPY assets ./assets
USER 10001:10001
CMD ["python", "main.py"]
