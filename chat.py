import os

import streamlit as st
from openai import OpenAI
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime

from simplechat import temperature

# === НАСТРОЙКА СТРАНИЦЫ ===
st.set_page_config(page_title="AI Чат", page_icon="🤖")
st.title("💬 AI Ассистент")

# === НАСТРОЙКА БАЗЫ ДАННЫХ ===
# Замените на ваши credentials
# Формат: postgresql://user:password@host:port/database
DEFAULT_DATABASE_URL = "postgresql://ilia:begemot@gek:5432/aichat"

DATABASE_URL = os.getenv("DB_URL", DEFAULT_DATABASE_URL)

engine = create_engine(DATABASE_URL)
Base = declarative_base()


# === МОДЕЛИ БД ===
class Chat(Base):
    __tablename__ = 'chats'

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    messages = relationship("Message", back_populates="chat", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = 'messages'

    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey('chats.id'), nullable=False)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    chat = relationship("Chat", back_populates="messages")


# Создаем таблицы при первом запуске
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)


# === ФУНКЦИИ РАБОТЫ С БД ===
def create_new_chat():
    """Создает новый чат и возвращает его ID"""
    session = Session()
    try:
        chat = Chat(title=f"Чат {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        session.add(chat)
        session.commit()
        chat_id = chat.id
        return chat_id
    finally:
        session.close()


def save_message(chat_id, role, content):
    """Сохраняет сообщение в БД"""
    session = Session()
    try:
        message = Message(chat_id=chat_id, role=role, content=content)
        session.add(message)
        session.commit()
    finally:
        session.close()


def load_chat_history(chat_id):
    """Загружает историю чата из БД"""
    session = Session()
    try:
        messages = session.query(Message).filter_by(chat_id=chat_id).order_by(Message.created_at).all()
        result = [{"role": m.role, "content": m.content} for m in messages]
        return result
    finally:
        session.close()


def get_all_chats():
    """Получает список всех чатов"""
    session = Session()
    try:
        chats = session.query(Chat).order_by(Chat.created_at.desc()).all()
        result = [(c.id, c.title, c.created_at) for c in chats]
        return result
    finally:
        session.close()


# === ИНИЦИАЛИЗАЦИЯ OPENAI ===
# API ключ берется из переменной окружения OPENAI_API_KEY или из .streamlit/secrets.toml
url = "http://localhost:8000/v1"
client = OpenAI(
    base_url=url,  # адрес vLLM сервера
    api_key="no-key",  # любой непустой строки, если не задан --api-key
    timeout=240.0
)

model = client.models.list().data[0]
model_name = model.model_extra['root']
max_model_len = model.model_extra["max_model_len"]

# === УПРАВЛЕНИЕ СОСТОЯНИЕМ ===
# Если нет активного чата, создаем новый или загружаем последний
if "chat_id" not in st.session_state:
    chats = get_all_chats()
    if chats:
        st.session_state.chat_id = chats[0][0]  # Берем последний чат
        st.session_state.messages = load_chat_history(st.session_state.chat_id)
    else:
        # Создаем новый чат
        st.session_state.chat_id = create_new_chat()
        st.session_state.messages = [
            {"role": "system", "content": "Ты дружелюбный AI-ассистент."},
            {"role": "assistant", "content": "Привет! Чем могу помочь?"}
        ]

# === САЙДБАР: УПРАВЛЕНИЕ ЧАТАМИ ===

with st.sidebar:
    st.header("📚 Мои чаты")

    # Кнопка создания нового чата
    if st.button("➕ Новый чат"):
        st.session_state.chat_id = create_new_chat()
        st.session_state.messages = [
            {"role": "system", "content": "Ты дружелюбный AI-ассистент."},
            {"role": "assistant", "content": "Привет! Это новый чат. Чем могу помочь?"}
        ]
        st.rerun()

    temperature = st.slider("Креативность (Temperature)", key = "temp", min_value=0.0, max_value=1.0, value=0.7, step=0.1)
    st.divider()

    # Список существующих чатов
    chats = get_all_chats()
    for chat_id, title, created_at in chats:
        if st.button(f"💬 {title}", key=f"chat_{chat_id}"):
            st.session_state.chat_id = chat_id
            st.session_state.messages = load_chat_history(chat_id)
            st.rerun()

    st.divider()
    st.caption(f"Текущий чат ID: {st.session_state.chat_id}")

# === ОТОБРАЖЕНИЕ ИСТОРИИ ===
for message in st.session_state.messages:
    if message["role"] != "system":
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

# === ОБРАБОТКА ВВОДА ПОЛЬЗОВАТЕЛЯ ===
if prompt := st.chat_input("Напишите сообщение..."):
    # Добавляем сообщение пользователя в UI
    with st.chat_message("user"):
        st.markdown(prompt)

    # Сохраняем в session_state
    st.session_state.messages.append({"role": "user", "content": prompt})

    # Сохраняем в БД
    save_message(st.session_state.chat_id, "user", prompt)

    # Генерируем ответ от AI
    with st.chat_message("assistant"):
        stream = client.chat.completions.create(
            model=model.id,
            messages=[
                {"role": m["role"], "content": m["content"]}
                for m in st.session_state.messages
            ],
            temperature=temperature,
            stream=True,
        )
        response = st.write_stream(stream)

    # Сохраняем ответ в session_state
    st.session_state.messages.append({"role": "assistant", "content": response})

    # Сохраняем ответ в БД
    save_message(st.session_state.chat_id, "assistant", response)