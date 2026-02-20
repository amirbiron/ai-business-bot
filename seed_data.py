"""
Seed Data — Populates the knowledge base with demo data for "Dana's Beauty Salon".

Run this script to initialize the database with example business information.
Usage: python -m ai_chatbot.seed_data
"""

import logging
from ai_chatbot import database as db
from ai_chatbot.rag.engine import rebuild_index

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ─── Demo Knowledge Base Entries ──────────────────────────────────────────────

DEMO_ENTRIES = [
    # ── Services ──────────────────────────────────────────────────────────
    {
        "category": "Services",
        "title": "Hair Services",
        "content": """Dana's Beauty Salon offers a full range of professional hair services:

HAIRCUTS:
- Women's Haircut & Style: Includes consultation, wash, cut, and blow-dry
- Men's Haircut: Classic or modern styles with precision cutting
- Children's Haircut (under 12): Gentle and fun experience for kids
- Bang Trim: Quick trim for bangs/fringe between appointments

HAIR COLORING:
- Full Color: Complete single-process color application
- Highlights / Lowlights: Foil or balayage technique
- Balayage: Hand-painted highlights for a natural, sun-kissed look
- Root Touch-Up: Color refresh for roots only
- Color Correction: Fix previous color treatments (consultation required)

HAIR TREATMENTS:
- Deep Conditioning Treatment: Intensive moisture and repair
- Keratin Smoothing Treatment: Reduces frizz for 3-4 months
- Scalp Treatment: Targeted treatment for scalp health
- Olaplex Treatment: Bond-building treatment for damaged hair

STYLING:
- Blow-Dry & Style: Professional blow-out styling
- Special Occasion Updo: For weddings, proms, and events
- Bridal Hair: Wedding day hair styling (trial included)"""
    },
    {
        "category": "Services",
        "title": "Nail Services",
        "content": """Our nail services include:

MANICURE:
- Classic Manicure: Nail shaping, cuticle care, hand massage, and polish
- Gel Manicure: Long-lasting gel polish (2-3 weeks wear)
- Luxury Spa Manicure: Extended treatment with exfoliation, mask, and hot towel

PEDICURE:
- Classic Pedicure: Foot soak, nail care, callus removal, and polish
- Gel Pedicure: Long-lasting gel polish for toes
- Luxury Spa Pedicure: Full treatment with sugar scrub, mask, and extended massage

NAIL ART:
- Simple Nail Art: Accent nails with basic designs
- Full Nail Art: Custom designs on all nails
- French Tips: Classic or colored French manicure

NAIL EXTENSIONS:
- Acrylic Full Set: Full set of acrylic nail extensions
- Acrylic Fill: Maintenance fill for existing acrylics
- Gel Extensions: Soft gel nail extensions"""
    },
    {
        "category": "Services",
        "title": "Facial & Skin Services",
        "content": """Facial and skin care services at Dana's Beauty Salon:

FACIALS:
- Classic Facial: Deep cleansing, exfoliation, extraction, mask, and moisturizer (60 min)
- Anti-Aging Facial: Targeted treatment with collagen and peptides (75 min)
- Hydrating Facial: Intense moisture boost for dry skin (60 min)
- Acne Treatment Facial: Deep cleansing with salicylic acid and blue light (60 min)
- Express Facial: Quick refresh facial for busy schedules (30 min)

SKIN TREATMENTS:
- Microdermabrasion: Crystal-free exfoliation for smoother skin
- Chemical Peel (Light): Gentle peel for brightening and texture
- LED Light Therapy: Red or blue light for anti-aging or acne
- Dermaplaning: Gentle exfoliation removing peach fuzz"""
    },
    {
        "category": "Services",
        "title": "Waxing & Hair Removal",
        "content": """Waxing and hair removal services:

FACE:
- Eyebrow Wax: Shaping and clean-up
- Upper Lip Wax
- Full Face Wax: Eyebrows, upper lip, chin, and sides

BODY:
- Underarm Wax
- Half Arm Wax
- Full Arm Wax
- Half Leg Wax
- Full Leg Wax
- Brazilian Wax
- Bikini Line Wax
- Back Wax
- Chest Wax

We use premium hard wax and soft wax depending on the area for maximum comfort and effectiveness. All waxing services include pre-wax cleansing and post-wax soothing lotion."""
    },

    # ── Pricing ───────────────────────────────────────────────────────────
    {
        "category": "Pricing",
        "title": "Summer 2025 Price List",
        "content": """DANA'S BEAUTY SALON — PRICE LIST (Valid from June 2025)

HAIR SERVICES:
- Women's Haircut & Style: $65
- Men's Haircut: $35
- Children's Haircut (under 12): $25
- Bang Trim: $15
- Full Color: $95-$130 (depending on length)
- Highlights / Lowlights: $120-$180
- Balayage: $150-$220
- Root Touch-Up: $75
- Color Correction: Starting at $200 (consultation required)
- Deep Conditioning Treatment: $40
- Keratin Smoothing Treatment: $250-$350
- Olaplex Treatment: $50 (add-on: $30)
- Blow-Dry & Style: $45
- Special Occasion Updo: $85
- Bridal Hair (with trial): $200

NAIL SERVICES:
- Classic Manicure: $30
- Gel Manicure: $45
- Luxury Spa Manicure: $55
- Classic Pedicure: $40
- Gel Pedicure: $55
- Luxury Spa Pedicure: $70
- Simple Nail Art: +$10
- Full Nail Art: +$25-$40
- French Tips: +$10
- Acrylic Full Set: $65
- Acrylic Fill: $40
- Gel Extensions: $75

FACIAL & SKIN:
- Classic Facial (60 min): $85
- Anti-Aging Facial (75 min): $120
- Hydrating Facial (60 min): $90
- Acne Treatment Facial (60 min): $95
- Express Facial (30 min): $50
- Microdermabrasion: $110
- Chemical Peel (Light): $95
- LED Light Therapy: $60
- Dermaplaning: $75

WAXING:
- Eyebrow Wax: $18
- Upper Lip Wax: $12
- Full Face Wax: $45
- Underarm Wax: $25
- Half Leg Wax: $40
- Full Leg Wax: $65
- Brazilian Wax: $55
- Bikini Line Wax: $35

All prices include applicable taxes. Prices may vary based on hair length and thickness.
We accept cash, credit cards, and Apple Pay."""
    },

    # ── Hours ─────────────────────────────────────────────────────────────
    {
        "category": "Hours",
        "title": "Opening Hours",
        "content": """DANA'S BEAUTY SALON — OPENING HOURS

Regular Hours:
- Monday: 9:00 AM - 7:00 PM
- Tuesday: 9:00 AM - 7:00 PM
- Wednesday: 9:00 AM - 8:00 PM (Late night!)
- Thursday: 9:00 AM - 7:00 PM
- Friday: 9:00 AM - 5:00 PM
- Saturday: 9:00 AM - 4:00 PM
- Sunday: CLOSED

Holiday Hours:
- We are closed on all major national holidays
- Special holiday hours will be announced on our social media
- During holiday seasons, we recommend booking at least 2 weeks in advance

Last appointment is accepted 1 hour before closing time.
Walk-ins are welcome but appointments are recommended to avoid waiting."""
    },

    # ── Location ──────────────────────────────────────────────────────────
    {
        "category": "Location",
        "title": "Address & Directions",
        "content": """DANA'S BEAUTY SALON — LOCATION

Address: 123 Rothschild Boulevard, Tel Aviv, Israel 6688101

Landmarks: Located between Allenby Street and Herzl Street, next to Café Noir.

Getting Here:
- By Car: Street parking available on Rothschild Blvd. Paid parking garage at 110 Rothschild (2 min walk).
- By Bus: Lines 5, 18, 61 stop at Rothschild/Allenby junction (1 min walk)
- By Train: Tel Aviv HaShalom station (15 min walk or short bus ride)
- By Bike: Tel-O-Fun bike station right outside the salon

The salon is on the ground floor with wheelchair accessibility.
Look for the pink and white awning with our logo!

Contact:
- Phone: +972-3-555-0123
- WhatsApp: +972-50-555-0123
- Email: hello@danasbeauty.com
- Instagram: @danasbeautysalon
- Website: www.danasbeauty.com"""
    },

    # ── Staff ─────────────────────────────────────────────────────────────
    {
        "category": "Staff",
        "title": "Our Team",
        "content": """MEET OUR TEAM AT DANA'S BEAUTY SALON

DANA COHEN — Owner & Senior Stylist
- 15+ years of experience in hair styling and coloring
- Specializes in balayage, color correction, and bridal hair
- Certified by L'Oréal Professionnel and Wella
- Available: Monday-Friday

MAYA LEVI — Hair Stylist
- 8 years of experience
- Specializes in precision cuts and keratin treatments
- Expert in curly hair techniques (DevaCurl certified)
- Available: Monday-Saturday

NOOR HASSAN — Nail Technician
- 6 years of experience in nail art and extensions
- Certified in gel and acrylic techniques
- Known for creative nail art designs
- Available: Tuesday-Saturday

YAEL MIZRAHI — Esthetician
- Licensed esthetician with 10 years of experience
- Specializes in anti-aging facials and skin treatments
- Certified in microdermabrasion and chemical peels
- Available: Monday, Wednesday, Thursday, Saturday

LIOR SHAPIRA — Waxing Specialist & Junior Stylist
- 4 years of experience
- Specializes in Brazilian waxing and full-body waxing
- Also trained in blow-dry styling
- Available: Monday-Friday"""
    },

    # ── Policies ──────────────────────────────────────────────────────────
    {
        "category": "Policies",
        "title": "Cancellation & Booking Policy",
        "content": """DANA'S BEAUTY SALON — CANCELLATION & BOOKING POLICY

BOOKING:
- Appointments can be booked via phone, WhatsApp, or through our Telegram bot
- A valid phone number is required for booking confirmation
- We recommend booking at least 3-5 days in advance, especially for weekends
- Bridal and special event appointments should be booked at least 2 weeks ahead

CANCELLATION POLICY:
- Free cancellation up to 24 hours before your appointment
- Cancellations within 24 hours will be charged a 50% cancellation fee
- No-shows will be charged the full service price
- First-time no-shows receive a warning; repeated no-shows may result in requiring a deposit for future bookings

LATE ARRIVALS:
- If you arrive more than 15 minutes late, we may need to reschedule or modify your service
- Please call us if you're running late so we can accommodate you

DEPOSITS:
- Bridal packages require a 30% non-refundable deposit at booking
- Color correction services require a $50 consultation deposit (applied to service)
- Group bookings (3+ people) require a 25% deposit"""
    },
    {
        "category": "Policies",
        "title": "Safety & Hygiene Policy",
        "content": """DANA'S BEAUTY SALON — SAFETY & HYGIENE

We take your health and safety seriously:

SANITATION:
- All tools are sterilized between clients using hospital-grade disinfectant
- Single-use items (files, buffers, wax strips) are disposed of after each client
- Stations are sanitized between appointments
- We use fresh towels and capes for every client

PRODUCTS:
- We use only professional-grade, salon-quality products
- All products are cruelty-free
- We carry hypoallergenic options for sensitive skin
- Product brands: L'Oréal Professionnel, Olaplex, OPI, CND, Dermalogica

ALLERGIES:
- Please inform us of any allergies before your appointment
- Patch tests are available 48 hours before color services
- We maintain a record of client allergies in our system

COVID-19 PROTOCOLS:
- Enhanced cleaning and ventilation
- Hand sanitizer available at entrance
- Staff health monitoring"""
    },

    # ── FAQ ───────────────────────────────────────────────────────────────
    {
        "category": "FAQ",
        "title": "Frequently Asked Questions",
        "content": """FREQUENTLY ASKED QUESTIONS — DANA'S BEAUTY SALON

Q: Do I need an appointment or do you accept walk-ins?
A: We accept walk-ins, but appointments are strongly recommended to guarantee availability. You can book via phone, WhatsApp, or our Telegram bot.

Q: How long does a typical hair coloring appointment take?
A: Single-process color takes about 1.5-2 hours. Highlights and balayage take 2-3 hours. Color correction may take 3-5 hours depending on the situation.

Q: Do you offer gift cards?
A: Yes! Gift cards are available in any amount starting from $25. They can be purchased in-salon or ordered by phone for delivery.

Q: Is there parking available?
A: Street parking is available on Rothschild Boulevard (metered). There's also a paid parking garage at 110 Rothschild, a 2-minute walk away.

Q: Do you offer bridal packages?
A: Yes! Our bridal package includes a trial session and day-of styling. We also offer packages for the bridal party. Book at least 2 weeks in advance.

Q: What products do you use?
A: We use professional brands including L'Oréal Professionnel, Olaplex, OPI, CND, and Dermalogica. All our products are cruelty-free.

Q: Can I bring my child to the salon?
A: Children are welcome! We offer children's haircuts for kids under 12. For safety, children must be supervised at all times.

Q: Do you offer student discounts?
A: Yes! Students get 10% off all services with a valid student ID. This cannot be combined with other promotions.

Q: What payment methods do you accept?
A: We accept cash, all major credit cards, and Apple Pay. We do not accept personal checks.

Q: How often should I get a haircut?
A: We recommend every 6-8 weeks for most styles. If you're growing your hair out, every 8-12 weeks for a trim to keep it healthy."""
    },

    # ── Promotions ────────────────────────────────────────────────────────
    {
        "category": "Promotions",
        "title": "Current Promotions & Offers",
        "content": """CURRENT PROMOTIONS AT DANA'S BEAUTY SALON (Summer 2025)

SUMMER SPECIAL:
- 15% off all hair coloring services (June-August 2025)
- Book any color service and get a free Olaplex treatment (worth $50)

NEW CLIENT OFFER:
- First-time clients receive 20% off their first service
- Refer a friend and both get $15 off your next visit

LOYALTY PROGRAM:
- Earn 1 point per $1 spent
- 100 points = $10 off your next service
- Birthday month: Double points on all services!

PACKAGE DEALS:
- "Pamper Package": Haircut + Facial + Manicure for $150 (save $30)
- "Bridal Glow Package": Facial + Manicure + Pedicure for $170 (save $25)
- "Monthly Maintenance": Gel Manicure + Eyebrow Wax for $55 (save $8)

STUDENT DISCOUNT:
- 10% off all services with valid student ID
- Cannot be combined with other promotions

WEDNESDAY LATE NIGHT SPECIAL:
- 10% off all services booked after 6:00 PM on Wednesdays"""
    },
]


def seed_database():
    """Populate the database with demo data."""
    logger.info("Initializing database...")
    db.init_db()
    
    # Check if data already exists
    existing = db.get_all_kb_entries()
    if existing:
        logger.info("Database already has %s entries. Skipping seed.", len(existing))
        logger.info("To re-seed, delete the database file first: ai_chatbot/data/chatbot.db")
        return False
    
    logger.info("Seeding %s knowledge base entries...", len(DEMO_ENTRIES))
    
    for entry in DEMO_ENTRIES:
        entry_id = db.add_kb_entry(
            category=entry["category"],
            title=entry["title"],
            content=entry["content"],
        )
        logger.info(
            "  Added: [%s] %s (ID: %s)",
            entry["category"],
            entry["title"],
            entry_id,
        )
    
    logger.info("Seed data inserted successfully!")
    return True


def seed_and_index():
    """Seed the database and build the RAG index."""
    was_seeded = seed_database()
    
    if was_seeded:
        logger.info("Building RAG index...")
        rebuild_index()
        logger.info("RAG index built successfully!")
    else:
        logger.info("Checking if RAG index needs rebuilding...")
        from ai_chatbot.rag.vector_store import get_vector_store
        store = get_vector_store()
        if store.index is None or store.index.ntotal == 0:
            logger.info("Index is empty. Rebuilding...")
            rebuild_index()
            logger.info("RAG index built successfully!")
        else:
            logger.info("RAG index already exists.")


if __name__ == "__main__":
    seed_and_index()
