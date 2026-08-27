import os
from sqlalchemy import create_engine, Column, BigInteger, String, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///bot.db")
# Render PostgreSQL-এর URL অনেক সময় postgres:// দিয়ে শুরু হয়, সেটাকে postgresql:// করা লাগে
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class Admin(Base):
    __tablename__ = 'admins'
    telegram_id = Column(BigInteger, primary_key=True)

class Vendor(Base):
    __tablename__ = 'vendors'
    telegram_id = Column(BigInteger, primary_key=True)
    name = Column(String)
    latitude = Column(Float)
    longitude = Column(Float)
    is_suspended = Column(Boolean, default=False)

class Rider(Base):
    __tablename__ = 'riders'
    telegram_id = Column(BigInteger, primary_key=True)
    name = Column(String)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    is_online = Column(Boolean, default=False)
    is_busy = Column(Boolean, default=False)
    is_suspended = Column(Boolean, default=False)

class DeliveryRequest(Base):
    __tablename__ = 'delivery_requests'
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    vendor_id = Column(BigInteger, ForeignKey('vendors.telegram_id'))
    details = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="PENDING") # PENDING, ACCEPTED, COMPLETED, CANCELLED
    current_rider_id = Column(BigInteger, nullable=True)
    accepted_rider_id = Column(BigInteger, nullable=True)
    rejected_riders = Column(String, default="") # Comma separated rider IDs
    message_id = Column(BigInteger, nullable=True) # Message ID sent to rider

def init_db():
    Base.metadata.create_all(engine)
