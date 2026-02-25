from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from fastapi.middleware.cors import CORSMiddleware
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- AI CONFIG ----------------

USE_AI = os.getenv("USE_AI", "false").lower() == "true"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = None
if USE_AI:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing in .env")
    client = OpenAI(api_key=OPENAI_API_KEY)


# ---------------- DB DEP ----------------

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------- PRIORITY ----------------

def calculate_priority(weight: float, due_date: datetime):
    now = datetime.now(timezone.utc)

    if due_date.tzinfo is None:
        due_date = due_date.replace(tzinfo=timezone.utc)

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


@app.get("/me")
def read_current_user(current_user: models.User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email
    }


# ---------------- ASSIGNMENTS ----------------

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
        is_completed=False,
        completed_at=None,
    )

    db.add(db_assignment)
    db.commit()
    db.refresh(db_assignment)

    return db_assignment


@app.get("/assignments", response_model=list[schemas.AssignmentResponse])
def get_assignments(
    include_completed: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.Assignment).filter(
        models.Assignment.user_id == current_user.id
    )

    if not include_completed:
        query = query.filter(models.Assignment.is_completed == False)

    return query.order_by(models.Assignment.priority_score.desc()).all()


@app.patch("/assignments/{assignment_id}/toggle")
def toggle_assignment_completion(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    assignment = db.query(models.Assignment).filter(
        models.Assignment.id == assignment_id,
        models.Assignment.user_id == current_user.id
    ).first()

    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    assignment.is_completed = not assignment.is_completed

    if assignment.is_completed:
        assignment.completed_at = datetime.now(timezone.utc)
    else:
        assignment.completed_at = None

    db.commit()
    db.refresh(assignment)

    return {
        "id": assignment.id,
        "is_completed": assignment.is_completed
    }


# ---------------- SYLLABUS UPLOAD ----------------

@app.post("/upload-syllabus")
async def upload_syllabus(
    file: UploadFile = File(...),
    dry_run: bool = False,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    contents = await file.read()
    text = contents.decode("utf-8", errors="ignore")

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
                        "due_date": datetime.now(timezone.utc).isoformat(),
                        "weight": weight,
                        "description": "Extracted locally (mock mode)"
                    })
                except:
                    continue

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
            due_date = datetime.now(timezone.utc)

        priority = calculate_priority(item["weight"], due_date)

        db_assignment = models.Assignment(
            user_id=current_user.id,
            course_name=item["course_name"],
            title=item["title"],
            due_date=due_date,
            weight=item["weight"],
            description=item["description"],
            priority_score=priority,
            is_completed=False,
            completed_at=None,
        )

        if not dry_run:
            db.add(db_assignment)
            db.commit()
            db.refresh(db_assignment)

        created.append(db_assignment)

    return created