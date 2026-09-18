"""Seed clearly-fictional demo data for development and staging.

All names, emails, and photos are invented — monogram avatars, example.com
addresses. Never run with SEED_DEMO_DATA against production.

Usage: SEED_DEMO_DATA=true python -m app.seed
"""
import io
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from .config import get_settings
from .db import Base, SessionLocal, engine
from .models import Application, Event, Member, Profile, User
from .security import utcnow
from .services.admissions import set_setting
from .storage import put

PALETTE = ["#33493a", "#7a4b33", "#4b5e7a", "#6b5b8c", "#8c6a3a", "#3a6b64"]

FICTIONAL = [
    ("Mara Chen", "mara.chen@example.com", "Researcher", "Meridian Labs",
     "Evaluating long-horizon agent behavior in scientific workflows.",
     "Former particle physicist now building evals for autonomous research agents.",
     ["AI research", "Science", "Agents"], ["evals", "physics", "experiment design"],
     "Sanity-checking an eval design; intros to the NYC physics diaspora.",
     "are thinking about post-training for honesty.",
     "cycling routes out of the city, natural wine"),
    ("Dev Okafor", "dev.okafor@example.com", "Founder", "Fieldnote",
     "Building a quiet tool for field scientists to structure observations.",
     "Second-time founder; first company was acquired. Writes about small software.",
     ["Startups", "Applied AI", "Climate"], ["product", "embedded ML", "fundraising"],
     "Early product decisions; what not to build.",
     "are applying AI to unglamorous industries.",
     "Nigerian literature, sourdough"),
    ("Ines Barros", "ines.barros@example.com", "Policy Fellow", "Horizon Institute",
     "Writing about compute governance and what democracies should want from AI.",
     "Spent five years in Brussels tech policy; moved to NYC for the salons.",
     ["AI policy", "Writing"], ["EU regulation", "compute policy"],
     "A crash course in Brussels procedure; editing long documents.",
     "can explain hardware to her without condescension.",
     "fado, middle-distance running"),
    ("Theo Lindqvist", "theo.l@example.com", "Engineer", "Aster Robotics",
     "Teaching warehouse robots to recover gracefully from their own mistakes.",
     "Robotics engineer who thinks failure modes are the interesting part.",
     ["Robotics", "Applied AI"], ["controls", "sim2real", "fleet ops"],
     "War stories about robots in the wild.",
     "think about AI risk without losing their sense of humor.",
     "alpine skiing, mechanical watches"),
    ("Priya Raman", "priya.raman@example.com", "Partner", "Cobalt Ventures",
     "Investing in AI infrastructure and tooling for the next platform shift.",
     "Venture investor; previously built devtools. Happy to be argued with.",
     ["Venture", "Startups", "Agents"], ["infra", "devtools", "market maps"],
     "A second opinion on a technical pitch.",
     "are technical founders early enough to still be scared.",
     "tennis, hotel breakfasts"),
    ("Jonah Weiss", "jonah.weiss@example.com", "PhD candidate", "NYU",
     "Mechanistic interpretability of small reasoning models.",
     "Grad student. Takes notes on everything, including dinners.",
     ["AI research", "Interpretability", "AI safety"], ["interp", "PyTorch", "note-taking systems"],
     "Walkthroughs of recent interp papers.",
     "can tell him which of his ideas are already papers.",
     "NYU trivia, baking bread at altitude"),
    ("Sofia Marchetti", "sofia.m@example.com", "Creative Director", "Studio Onda",
     "Making AI tools that respect craft — and occasionally argue back.",
     "Design director exploring generative tools that don't flatten taste.",
     ["Creative tools", "Applied AI"], ["design systems", "generative media"],
     "An honest critique of a demo's aesthetics.",
     "are engineers who care about typography.",
     "Italian cinema, letterpress printing"),
    ("Marcus Bell", "marcus.bell@example.com", "Journalist", "The Signal Review",
     "Covering AI deployment in hospitals, slowly and carefully.",
     "Reporter. Writes the piece after the hype cycle ends.",
     ["Writing", "AI policy", "Biotech"], ["health tech", "FOIA", "narrative"],
     "A skeptical read of a press release.",
     "know where the bodies are buried in health AI.",
     "jazz drumming, Queens food courts"),
    ("Yuki Tanaka", "yuki.tanaka@example.com", "Researcher", "Independent",
     "Studying how small teams adopt AI agents for real work.",
     "Sociologist of technology, currently independent.",
     ["AI research", "Education"], ["ethnography", "fieldwork methods"],
     "An ethnographer's perspective on your team's AI rollout.",
     "are building tools people actually keep using.",
     "onigiri, rail travel"),
    ("Elena Vasquez", "elena.v@example.com", "CTO", "Northfield Bio",
     "Applying foundation models to protein annotation pipelines.",
     "Computational biologist turned CTO.",
     ["Biotech", "Applied AI"], ["protein models", "data pipelines", "hiring"],
     "Bio meets AI translation; hiring for hybrid roles.",
     "can pressure-test whether a model result is real.",
     "caving, molecular gastronomy"),
]


def _monogram(name: str, i: int) -> bytes:
    img = Image.new("RGB", (800, 800), PALETTE[i % len(PALETTE)])
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", 340)
    except OSError:
        font = ImageFont.load_default()
    initials = "".join(w[0] for w in name.split()[:2])
    d.text((400, 390), initials, font=font, fill="#f6f1e8", anchor="mm")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def run() -> None:
    s = get_settings()
    if not s.seed_demo_data:
        raise SystemExit("Refusing to seed: SEED_DEMO_DATA=true is required.")
    if s.environment == "production":
        raise SystemExit("Refusing to seed in production.")
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        for i, (name, email, role, org, work, bio, ints, exp, come, meet, outside) in enumerate(FICTIONAL):
            user = db.query(User).filter_by(email=email).first()
            if user:
                continue
            user = User(email=email)
            db.add(user)
            db.flush()
            db.add(Member(user_id=user.id, status="active", approved_at=utcnow()))
            key = f"headshots/seed-{i}.jpg"
            put(key, _monogram(name, i), "image/jpeg")
            db.add(Profile(
                user_id=user.id, display_name=name, headline=work.split(".")[0] + ".",
                role=role, organization=org, working_on=work, bio=bio,
                interests=ints, expertise=exp, come_to_me_for=come,
                like_to_meet=meet, outside_ai=outside,
                contact_links=[{"label": "Email", "url": email}],
                photo_key=key, published=True, norms_accepted_at=utcnow(),
            ))
        for email in s.admin_email_list:
            if not db.query(User).filter_by(email=email).first():
                u = User(email=email, is_admin=True)
                db.add(u)

        ny = ZoneInfo("America/New_York")
        for days, title, host, desc in [
            (6, "Salon: What would make agents trustworthy?", "Mara",
             "An evening around one question — bring a position, leave with a better one."),
            (13, "Founders' breakfast", "Priya", "A small table for people building AI companies."),
        ]:
            if not db.query(Event).filter_by(title=title).first():
                db.add(Event(
                    title=title, host=host, description=desc,
                    starts_at=datetime.now(ny).replace(hour=18 if "Salon" in title else 8,
                                                       minute=30, second=0, microsecond=0)
                    .astimezone(UTC) + timedelta(days=days),
                    external_url="https://lu.ma/example",
                ))
        if not db.query(Application).filter_by(email="sam.kato@example.com").first():
            db.add(Application(
                name="Sam Kato", email="sam.kato@example.com",
                role_company="Designer, independent", link="https://samkato.example.com",
                working_on="Tools for attention, and what notification design owes people.",
                why_join="Looking for a room where AI is taken seriously and discussed warmly.",
                referrer="Mara Chen"))
        set_setting(db, "member_cap", str(s.member_cap))
        db.commit()
        print(f"Seeded {len(FICTIONAL)} fictional members + demo events.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
