import os
from dotenv import load_dotenv
load_dotenv()

CHAT_API_KEY=os.getenv('CHAT_API_KEY') or os.getenv('GEMINI_API_KEY')
DEFAULT_CHAT_MODEL='liquid/lfm-2.5-2.6b:free'

CHAT_MODEL=os.getenv('CHAT_MODEL') or None
JWT_SECRET=os.getenv('JWT_SECRET','change-me-in-production')
JWT_ALGORITHM='HS256' 
TOKEN_EXPIRE_HOURS=int(os.getenv('TOKEN_EXPIRE_HOURS','8'))
HASH_ITERATIONS=600_000

AUTH_DB_PATH=os.getenv('AUTH_DB_PATH','./auth.db')
PROJECTS_ROOT=os.getenv('PROJECTS_ROOT','./projects')

MAX_AGENT_STEPS=int(os.getenv('MAX_AGENT_STEPS','8'))
MAX_CONTEXT_CHARS=int(os.getenv('MAX_CONTEXT_CHARS','50000'))

COMPILER_MODEL=os.getenv('COMPILER_MODEL') or None