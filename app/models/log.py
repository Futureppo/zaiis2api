from sqlalchemy import Column, Integer, String, DateTime, Text, Float
from sqlalchemy.sql import func
from app.db.session import Base

class RequestLog(Base):
    __tablename__ = "request_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    model = Column(String, index=True)
    chat_id = Column(String, index=True)
    status_code = Column(Integer)
    duration_ms = Column(Float)
    error_message = Column(Text, nullable=True)
    
    def __repr__(self):
        return f"<RequestLog(id={self.id}, model={self.model}, status={self.status_code})>"

