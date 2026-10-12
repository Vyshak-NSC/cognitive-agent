import os
from dotenv import load_dotenv
load_dotenv()

CHAT_API_KEY=os.getenv('CHAT_API_KEY') or os.getenv('GEMINI_API_KEY')
DEFAULT_CHAT_MODEL='gemini-3.5-flash-lite'

CHAT_MODEL=os.getenv('CHAT_MODEL') or None
JWT_SECRET=os.getenv('JWT_SECRET','change-me-in-production')
JWT_ALGORITHM='HS256' 
TOKEN_EXPIRE_HOURS=int(os.getenv('TOKEN_EXPIRE_HOURS','8'))
HASH_ITERATIONS=600_000

AUTH_DB_PATH=os.getenv('AUTH_DB_PATH','./auth.db')
PROJECTS_ROOT=os.getenv('PROJECTS_ROOT','./projects')

MAX_AGENT_STEPS=int(os.getenv('MAX_AGENT_STEPS','20'))
MAX_CONTEXT_CHARS=int(os.getenv('MAX_CONTEXT_CHARS','50000'))

COMPILER_MODEL=os.getenv('COMPILER_MODEL') or None

# --- Cognition compiler fidelity knobs -------------------------------------
# Max characters of source text per compiler call. Smaller segments leave the
# model room to transcribe every fact instead of compressing a whole chapter
# into a few paragraphs of output.
COMPILER_SEGMENT_CHARS=int(os.getenv('COMPILER_SEGMENT_CHARS','8000'))
# Gemini via AI Studio has tighter per-request token limits, so it reads less per
# call and makes more, smaller calls.
COMPILER_SEGMENT_CHARS_GEMINI=int(os.getenv('COMPILER_SEGMENT_CHARS_GEMINI','4000'))
# Output-token ceiling for compiler JSON calls (providers that need one).
COMPILER_MAX_OUTPUT_TOKENS=int(os.getenv('COMPILER_MAX_OUTPUT_TOKENS','16000'))
# After each segment, deterministically find source sentences whose terms are
# missing from the model's output and ask for those facts once more.
COMPILER_GAP_FILL=os.getenv('COMPILER_GAP_FILL','1').strip().lower() not in ('0','false','no','off')