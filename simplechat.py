import streamlit as st
from openai import OpenAI
#import tiktoken


# 1. Настройка страницы
st.set_page_config(page_title="AI Генератор", page_icon="🤖", layout="centered")
st.markdown("Введите роль для модели и ваш запрос, чтобы получить результат.")

# 2. Боковая панель для настроек API
st.sidebar.header("⚙️ Настройки API")
api_key = "none"
#url = "https://ai.vishenki.crazedns.ru/v1"
url = "http://localhost:8000/v1"
temperature = st.sidebar.slider("Креативность (Temperature)", min_value=0.0, max_value=1.0, value=0.7, step=0.1)

# Опционально: возможность изменить базовый URL (для совместимых API, например, LocalAI или Azure)
base_url = st.sidebar.text_input("Base URL (оставьте пустым для стандартного OpenAI)", value="")

# 3. Основная форма ввода
st.markdown("---")
col1, col2 = st.columns([1, 2])

with col1:
    role = st.text_input(
        "🎭 Роль (System Prompt)",
        value="Ты полезный, вежливый и экспертный ИИ-ассистент.",
        help="Задает поведение и контекст для модели."
    )

with col2:
    user_text = st.text_area(
        "📝 Ваш запрос (User Prompt)",
        height=150,
        placeholder="Например: Напиши краткое резюме этой статьи...",
        help="Основной текст, который нужно обработать."
    )

# 4. Логика отправки запроса
if st.button("🚀 Сгенерировать ответ", type="primary", use_container_width=True):
    if not user_text.strip():
        st.warning("⚠️ Поле с текстом запроса не может быть пустым!")
    else:
        # Инициализация клиента
        client = OpenAI(
            base_url=url,  # адрес vLLM сервера
            api_key="no-key",  # любой непустой строки, если не задан --api-key
            timeout=240.0
        )

        model = client.models.list().data[0]
        model_name = model.model_extra['root']
        max_model_len = model.model_extra["max_model_len"]

        #encoding = tiktoken.encoding_for_model(model_name)
        #tokens  = len(encoding.encode(user_text))
        tokens = "na"
        st.markdown(f"Model: {model_name}\n max_model_len={max_model_len} tokens = {tokens}")

        # Индикатор загрузки
        with st.spinner("🔄 Модель думает... Пожалуйста, подождите."):
            try:
                # Вызов API
                response = client.chat.completions.create(
                    model=model.id,
                    messages=[
                        {"role": "system", "content": role},
                        {"role": "user", "content": user_text}
                    ],
                    temperature=temperature
                )

                # Извлечение результата
                result_text = response.choices[0].message.content

                # Отображение результата
                st.success("✅ Успешно сгенерировано!")
                st.markdown("### 💡 Результат:")
                st.markdown(result_text)  # markdown автоматически форматирует жирный текст, списки и код

                # Добавляем кнопку для копирования (визуальный хак через code block)
                st.code(result_text, language="text")

            except Exception as e:
                st.error(f"❌ Произошла ошибка при запросе к API:")
                st.exception(e)  # Показывает детальный traceback для отладки