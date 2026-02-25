from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import os
import json

from openai import OpenAI

from backend.database import SessionLocal, engine
import backend.models as models
import backend.schemas as schemas
from backend.auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
)

# Initialize database
models.Base.metadata.create_all(bind=engine)

app = FastAPI()

# AI config
USE_AI = os.getenv("USE_AI", "false").lower() == "true"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = None
if USE_AI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing in .env")
    client = OpenAI(api_key=OPENAI_API_KEY)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def calculate_priority(weight: float, due_date: datetime):
    now = datetime.now(timezone.utc)
    days_until_due = (due_date - now).days

    if days_until_due <= 0:
        return weight * 10
    return weight * (1 / days_until_due)


@app.get("/")
def read_root():
    return {"status": "Backend operational"}


# ---------------- AUTH ----------------

@app.post("/auth/register")
def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    existing = db.query(models.User).filter(models.User.email == user.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    new_user = models.User(
        email=user.email,
        hashed_password=hash_password(user.password),
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    token = create_access_token(subject=new_user.email)
    return {"access_token": token, "token_type": "bearer"}


@app.post("/auth/login", response_model=schemas.Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == form.username).first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(subject=user.email)
    return {"access_token": token, "token_type": "bearer"}


# ---------------- ASSIGNMENTS (PROTECTED) ----------------

@app.post("/assignments", response_model=schemas.AssignmentResponse)
def create_assignment(
    assignment: schemas.AssignmentCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    priority = calculate_priority(assignment.weight, assignment.due_date)

    db_assignment = models.Assignment(
        user_id=current_user.id,
        course_name=assignment.course_name,
        title=assignment.title,
        due_date=assignment.due_date,
        weight=assignment.weight,
        description=assignment.description,
        priority_score=priority,
    )

    db.add(db_assignment)
    db.commit()
    db.refresh(db_assignment)

    return db_assignment


@app.get("/assignments", response_model=list[schemas.AssignmentResponse])
def get_assignments(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return (
        db.query(models.Assignment)
        .filter(models.Assignment.user_id == current_user.id)
        .order_by(models.Assignment.priority_score.desc())
        .all()
    )


# ---------------- UPLOAD SYLLABUS (PROTECTED) ----------------

@app.post("/upload-syllabus")
async def upload_syllabus(
    file: UploadFile = File(...),
    dry_run: bool = False,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    contents = await file.read()
    text = contents.decode("utf-8", errors="ignore")

    # MOCK MODE
    if not USE_AI:
        lines = text.split("\n")
        assignments = []

        course_name = lines[0].strip() if lines else "Unknown Course"

        for line in lines:
            if "-" in line and "%" in line:
                try:
                    parts = line.split("-")
                    title = parts[0].strip()
                    weight = float(parts[-1].replace("%", "").strip())

                    assignments.append({
                        "course_name": course_name,
                        "title": title,
                        "due_date": datetime.utcnow().isoformat(),
                        "weight": weight,
                        "description": "Extracted locally (mock mode)"
                    })
                except:
                    continue

    # AI MODE
    else:
        prompt = f"""
Extract all assignments from this syllabus.
Return ONLY valid JSON list in this format:

[
  {{
    "course_name": "string",
    "title": "string",
    "due_date": "YYYY-MM-DDTHH:MM:SS",
    "weight": number,
    "description": "string"
  }}
]

Syllabus:
{text}
"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )

        ai_text = response.choices[0].message.content
        assignments = json.loads(ai_text)

    created = []

    for item in assignments:
        try:
            due_date = datetime.fromisoformat(item["due_date"])
        except:
            due_date = datetime.utcnow()

        priority = calculate_priority(item["weight"], due_date)

        db_assignment = models.Assignment(
            user_id=current_user.id,
            course_name=item["course_name"],
            title=item["title"],
            due_date=due_date,
            weight=item["weight"],
            description=item["description"],
            priority_score=priority,
        )

        if not dry_run:
            db.add(db_assignment)
            db.commit()
            db.refresh(db_assignment)

        created.append(db_assignment)

    return created