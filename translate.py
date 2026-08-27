import streamlit as st
import PyPDF2
import io
from fpdf import FPDF
from openai import OpenAI
from typing import List
import time


url = "http://localhost:8000/v1"
client = OpenAI(
    base_url=url,  # адрес vLLM сервера
    api_key="no-key",  # любой непустой строки, если не задан --api-key
    timeout=240.0
)

model = client.models.list().data[0]
model_name = model.model_extra['root']
max_model_len = model.model_extra["max_model_len"]
# Настройка страницы
st.set_page_config(page_title="PDF Переводчик", page_icon="📄", layout="wide")

# Инициализация сессионного состояния
if "translated_text" not in st.session_state:
    st.session_state.translated_text = None
if "original_text" not in st.session_state:
    st.session_state.original_text = None

st.title("📄 PDF Переводчик с AI")

# Боковая панель для настроек
with st.sidebar:
    st.header("⚙️ Настройки")
    #api_key = st.text_input("OpenAI API Key", type="password")
    target_language = st.selectbox(
        "Целевой язык",
        ["Русский", "English", "Español", "Français", "Deutsch", "中文"]
    )
    #model = st.selectbox(
    #    "Модель",
    #    ["gpt-3.5-turbo", "gpt-4", "gpt-4-turbo-preview"]
    #)

# Загрузка PDF
uploaded_file = st.file_uploader("Загрузите PDF файл", type="pdf")


def extract_text_from_pdf(pdf_file) -> str:
    """Извлекает текст из PDF файла"""
    pdf_reader = PyPDF2.PdfReader(pdf_file)
    text = ""
    for page in pdf_reader.pages:
        text += page.extract_text() + "\n"
    return text


def split_text_into_chunks(text: str, max_tokens: int = 3000) -> List[str]:
    """Разбивает текст на части для обработки"""
    # Примерное разделение по символам (1 токен ≈ 4 символа для английского, 2-3 для русского)
    chunk_size = max_tokens * 3
    chunks = []

    for i in range(0, len(text), chunk_size):
        chunk = text[i:i + chunk_size]
        if chunk.strip():
            chunks.append(chunk)

    return chunks


def translate_text(text: str, target_lang: str, api_key: str, model) -> str:
    """Отправляет текст в AI модель для перевода"""

    prompt = f"""Переведи следующий текст на {target_lang}. 
    Сохрани форматирование и структуру текста.
    Переводи только содержание, не добавляй комментариев.

    Текст для перевода:
    {text}"""

    try:
        response = client.chat.completions.create(
            model=model.id,
            messages=[
                {"role": "system",
                 "content": "Ты профессиональный переводчик. Переводи точно и сохраняй смысл оригинала."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
        )
        return response.choices[0].message.content
    except Exception as e:
        st.error(f"Ошибка при переводе: {e}")
        return None


def create_pdf_from_text(text: str) -> bytes:
    """Создает PDF файл из текста"""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    # Разбиваем текст на строки и добавляем в PDF
    lines = text.split('\n')
    for line in lines:
        # Обработка длинных строк
        if len(line) > 0:
            pdf.multi_cell(0, 10, line)
        else:
            pdf.ln(5)

    # Сохраняем в память
    pdf_bytes = pdf.output(dest='S')
    if isinstance(pdf_bytes, str):
        pdf_bytes = pdf_bytes.encode('latin1')

    return bytes(pdf_bytes)


# Основная логика приложения
if uploaded_file is not None:
    st.success(f"✅ Файл загружен: {uploaded_file.name}")

    # Кнопка для обработки
    if st.button("🚀 Перевести PDF", type="primary"):
        api_key = "None"
        # Проверка API ключа
        if not api_key:
            st.error("❌ Пожалуйста, введите OpenAI API ключ в боковой панели")
        else:
            with st.spinner("📖 Извлечение текста из PDF..."):
                # Извлекаем текст
                original_text = extract_text_from_pdf(uploaded_file)
                st.session_state.original_text = original_text

                if not original_text.strip():
                    st.error("❌ Не удалось извлечь текст из PDF. Возможно, это сканированный документ.")
                else:
                    st.info(f"📝 Извлечено символов: {len(original_text)}")

                    # Разбиваем на части
                    chunks = split_text_into_chunks(original_text)
                    st.info(f"📦 Текст разбит на {len(chunks)} частей для обработки")

                    # Переводим каждую часть
                    translated_chunks = []
                    progress_bar = st.progress(0)

                    for i, chunk in enumerate(chunks):
                        with st.spinner(f"🔄 Перевод части {i + 1} из {len(chunks)}..."):
                            translated = translate_text(chunk, target_language, api_key, model)
                            if translated:
                                translated_chunks.append(translated)
                            else:
                                st.error(f"Ошибка при переводе части {i + 1}")
                                break

                        # Обновляем прогресс
                        progress_bar.progress((i + 1) / len(chunks))
                        time.sleep(0.5)  # Небольшая задержка для избежания rate limit

                    if len(translated_chunks) == len(chunks):
                        # Объединяем переведенные части
                        full_translated_text = "\n\n".join(translated_chunks)
                        st.session_state.translated_text = full_translated_text

                        st.success("✅ Перевод завершен!")

# Отображение результатов
if st.session_state.translated_text:
    st.markdown("---")

    # Вкладки для оригинала и перевода
    tab1, tab2 = st.tabs(["📄 Оригинал", "🌍 Перевод"])

    with tab1:
        st.text_area("Оригинальный текст", st.session_state.original_text, height=300)

    with tab2:
        st.text_area("Переведенный текст", st.session_state.translated_text, height=300)

    # Кнопка для скачивания
    st.markdown("---")

    # Создаем PDF
    pdf_bytes = create_pdf_from_text(st.session_state.translated_text)

    # Кнопка скачивания
    st.download_button(
        label="📥 Скачать переведенный PDF",
        data=pdf_bytes,
        file_name=f"translated_{uploaded_file.name}",
        mime="application/pdf"
    )

    # Кнопка очистки
    if st.button("🗑️ Очистить и начать заново"):
        st.session_state.translated_text = None
        st.session_state.original_text = None
        st.rerun()

# Информация в футере
st.markdown("---")
st.markdown("""
**Инструкция:**
1. Введите ваш OpenAI API ключ в боковой панели
2. Выберите целевой язык перевода
3. Загрузите PDF файл
4. Нажмите "Перевести PDF"
5. Скачайте результат

⚠️ **Примечание:** Для больших файлов перевод может занять несколько минут.
""")