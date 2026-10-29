"""Initial legacy PostgreSQL schema, frozen independently of application models."""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    statements = [
        """CREATE TABLE users (
            id SERIAL PRIMARY KEY, full_name VARCHAR(255) NOT NULL,
            job_title VARCHAR(255) NOT NULL DEFAULT '', email VARCHAR(255) NOT NULL,
            password_hash VARCHAR(255) NOT NULL, created_at DATE NOT NULL,
            role VARCHAR(32) NOT NULL, current_bookings JSONB NOT NULL)""",
        "CREATE UNIQUE INDEX ix_users_email ON users(email)",
        """CREATE TABLE desk_features (id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL, description VARCHAR(512) NOT NULL)""",
        """CREATE TABLE desks (id SERIAL PRIMARY KEY, floor INTEGER, section INTEGER,
            number INTEGER NOT NULL, is_free BOOLEAN NOT NULL, sockets INTEGER,
            features JSONB NOT NULL, CONSTRAINT uq_desks_floor_number UNIQUE(floor,number))""",
        "CREATE INDEX ix_desks_features ON desks USING gin(features)",
        """CREATE TABLE bookings (id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            desk_id INTEGER NOT NULL REFERENCES desks(id), planed_at TIMESTAMP NOT NULL,
            duration INTERVAL NOT NULL, created_at TIMESTAMP NOT NULL,
            is_cancelled BOOLEAN NOT NULL, cancellation_reason VARCHAR(512), cancelled_at TIMESTAMP)""",
        "CREATE INDEX ix_bookings_user_id ON bookings(user_id)",
        "CREATE INDEX ix_bookings_desk_id ON bookings(desk_id)",
        "CREATE INDEX ix_bookings_planed_at ON bookings(planed_at)",
        """CREATE TABLE equipment (id SERIAL PRIMARY KEY,
            desk_id INTEGER REFERENCES desks(id), assigned_user_id INTEGER REFERENCES users(id),
            type VARCHAR(128) NOT NULL, socket_serial VARCHAR(128), socket_needed BOOLEAN NOT NULL,
            status VARCHAR(32) NOT NULL, created_at TIMESTAMP NOT NULL)""",
        "CREATE UNIQUE INDEX ix_equipment_socket_serial ON equipment(socket_serial)",
        "CREATE INDEX ix_equipment_desk_id ON equipment(desk_id)",
        "CREATE INDEX ix_equipment_assigned_user_id ON equipment(assigned_user_id)",
        """CREATE TABLE tickets (id SERIAL PRIMARY KEY,
            author_id INTEGER NOT NULL REFERENCES users(id), equipment_id INTEGER NOT NULL REFERENCES equipment(id),
            description TEXT NOT NULL, photo_url VARCHAR(1024), status VARCHAR(32) NOT NULL,
            created_at TIMESTAMP NOT NULL)""",
        "CREATE INDEX ix_tickets_author_id ON tickets(author_id)",
        "CREATE INDEX ix_tickets_equipment_id ON tickets(equipment_id)",
        """CREATE TABLE reports (id SERIAL PRIMARY KEY, report_type VARCHAR(64) NOT NULL,
            generated_at TIMESTAMP NOT NULL, data JSONB NOT NULL,
            generated_by INTEGER REFERENCES users(id), status VARCHAR(32) NOT NULL)""",
        """CREATE TABLE auth_sessions (token_hash VARCHAR(64) PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at TIMESTAMP NOT NULL)""",
        "CREATE INDEX ix_auth_sessions_user_id ON auth_sessions(user_id)",
        "CREATE INDEX ix_auth_sessions_expires_at ON auth_sessions(expires_at)",
        """CREATE TABLE password_reset_codes (user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            code_hash VARCHAR(64) NOT NULL, expires_at TIMESTAMP NOT NULL)""",
        "CREATE INDEX ix_password_reset_codes_expires_at ON password_reset_codes(expires_at)",
    ]
    for statement in statements:
        op.execute(statement)


def downgrade():
    for name in ("password_reset_codes", "auth_sessions", "reports", "tickets", "equipment", "bookings", "desks", "desk_features", "users"):
        op.drop_table(name)
