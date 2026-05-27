# Supply Chain Analytics Backend - Architecture

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                            │
│  (Frontend, Postman, cURL, Tableau, External Systems)           │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FASTAPI APPLICATION                         │
│                         (main.py)                                │
│  • CORS Middleware                                              │
│  • Error Handling Middleware                                    │
│  • Logging Middleware                                           │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                       API ROUTES LAYER                           │
├─────────────────────────────────────────────────────────────────┤
│  /api/orders/*        │  Order lifecycle & analytics            │
│  /api/products/*      │  Product management                     │
│  /api/suppliers/*     │  Supplier management                    │
│  /api/forecast/*      │  Analytics & forecasting                │
│  /api/auth/*          │  Authentication                         │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    PYDANTIC VALIDATION                           │
│                      (schemas.py)                                │
│  • Input validation                                             │
│  • Data serialization                                           │
│  • Type checking                                                │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      SERVICE LAYER                               │
├─────────────────────────────────────────────────────────────────┤
│  OrderService            │  • Order CRUD                        │
│                          │  • Analytics integration             │
│                          │  • Preprocessing pipeline            │
├──────────────────────────┼──────────────────────────────────────┤
│  PreprocessingService    │  • Duration calculation              │
│                          │  • SLA validation                    │
│                          │  • Bottleneck detection              │
├──────────────────────────┼──────────────────────────────────────┤
│  ProductService          │  • Product operations                │
│  SupplierService         │  • Supplier operations               │
│  ExportService           │  • CSV/Data export                   │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    UTILITIES LAYER                               │
├─────────────────────────────────────────────────────────────────┤
│  time_calculator.py      │  • Duration calculations             │
│  sla_validator.py        │  • SLA breach detection              │
│  bottleneck_detector.py  │  • Bottleneck analysis               │
│  lifecycle_validator.py  │  • Timestamp validation              │
│  data_generator.py       │  • Sample data generation            │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    DATABASE LAYER                                │
├─────────────────────────────────────────────────────────────────┤
│  SQLAlchemy ORM          │  • Session management                │
│  Models:                 │  • Relationship mapping              │
│   • Order (with analytics fields)                               │
│   • Product              │  • Transaction handling              │
│   • Supplier                                                    │
│   • User                                                        │
│   • Inventory                                                   │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     DATABASE (SQLite/PostgreSQL)                 │
│  • Persistent storage                                           │
│  • ACID compliance                                              │
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow - Order Creation with Analytics

```
1. API Request
   ↓
2. Pydantic Validation (OrderCreate schema)
   ↓
3. OrderService.create_order()
   ↓
4. Convert to dictionary
   ↓
5. PreprocessingService.preprocess_order()
   ├─→ validate_order_lifecycle() ──→ Validate timestamps
   ├─→ calculate_all_durations() ──→ Calculate 5 durations
   ├─→ validate_sla() ───────────→ Check SLA breaches
   └─→ analyze_bottleneck() ─────→ Identify bottleneck
   ↓
6. Merge analytics into order data
   ↓
7. Create Order model instance
   ↓
8. Save to database
   ↓
9. Return OrderResponse (with analytics)
```

## Analytics Processing Pipeline

```
Raw Order Data
    │
    ├─→ order_placed_at
    ├─→ procurement_completed_at
    ├─→ processing_completed_at
    ├─→ dispatched_at
    └─→ delivered_at
         │
         ▼
┌────────────────────────┐
│  Lifecycle Validation  │
│  - Sequence check      │
│  - Negative duration   │
└───────────┬────────────┘
            │
            ▼
┌────────────────────────┐
│  Time Calculation      │
│  - Procurement: 30h    │
│  - Processing: 20h     │
│  - Dispatch: 10h       │
│  - Delivery: 60h       │
│  - Total: 120h         │
└───────────┬────────────┘
            │
            ▼
┌────────────────────────┐
│  SLA Validation        │
│  - Check thresholds    │
│  - Breach: True        │
│  - Stage: delivery     │
└───────────┬────────────┘
            │
            ▼
┌────────────────────────┐
│  Bottleneck Detection  │
│  - Compare durations   │
│  - Bottleneck: delivery│
└───────────┬────────────┘
            │
            ▼
Enhanced Order with Analytics
    │
    ├─→ procurement_time: 30.0
    ├─→ processing_time: 20.0
    ├─→ dispatch_time_duration: 10.0
    ├─→ delivery_time_duration: 60.0
    ├─→ total_time: 120.0
    ├─→ sla_breach: true
    ├─→ breached_stage: "delivery"
    └─→ bottleneck_stage: "delivery"
         │
         ▼
    Saved to Database
```

## Module Dependencies

```
main.py
  └─→ app.routes.*
       └─→ app.services.*
            ├─→ app.services.preprocessing_service
            │    └─→ app.utils.*
            │         ├─→ time_calculator
            │         ├─→ sla_validator
            │         ├─→ bottleneck_detector
            │         └─→ lifecycle_validator
            │
            └─→ app.models.*
                 └─→ app.database.connection
```

## API Endpoint Structure

```
/
├── /health                           GET
├── /docs                             GET (Swagger)
├── /redoc                            GET (ReDoc)
│
├── /api/auth/
│   ├── /register                     POST
│   ├── /login                        POST
│   └── /users/{id}                   GET
│
├── /api/orders/
│   ├── /                             POST, GET
│   ├── /{id}                         GET, DELETE
│   ├── /by-number/{number}           GET
│   ├── /status/{status}              GET
│   ├── /{id}/status                  PATCH
│   └── /analytics/
│       ├── /summary                  GET
│       ├── /sla-breaches             GET
│       └── /bottleneck/{stage}       GET
│
├── /api/products/
│   ├── /                             POST, GET
│   ├── /{id}                         GET, DELETE
│   ├── /sku/{sku}                    GET
│   └── /category/{category}          GET
│
├── /api/suppliers/
│   ├── /                             POST, GET
│   └── /{id}                         GET, DELETE
│
└── /api/forecast/
    ├── /overview                     GET
    ├── /bottleneck-analysis          GET
    └── /sla-compliance               GET
```

## Database Schema

```
┌─────────────────┐
│     users       │
├─────────────────┤
│ id              │─┐
│ username        │ │
│ email           │ │
│ hashed_password │ │
│ full_name       │ │
│ role            │ │
│ is_active       │ │
│ created_at      │ │
│ updated_at      │ │
└─────────────────┘ │
                    │
┌─────────────────┐ │
│   suppliers     │ │
├─────────────────┤ │
│ id              │─┤
│ supplier_name   │ │
│ contact_person  │ │
│ email           │ │
│ phone           │ │
│ address         │ │
│ city            │ │
│ country         │ │
│ rating          │ │
└─────────────────┘ │
        │           │
        │           │
┌─────────────────┐ │
│    products     │ │
├─────────────────┤ │
│ id              │─┤
│ product_name    │ │
│ sku             │ │
│ category        │ │
│ unit_price      │ │
│ supplier_id     │─┘
│ reorder_level   │
└─────────────────┘
        │
        │
┌─────────────────────────────────┐
│           orders                │
├─────────────────────────────────┤
│ id                              │
│ order_number                    │
│ product_id                      │───→ products.id
│ supplier_id                     │───→ suppliers.id
│ quantity                        │
│ unit_price                      │
│ total_amount                    │
│                                 │
│ === Lifecycle Timestamps ===    │
│ order_placed_at                 │
│ procurement_completed_at        │
│ processing_completed_at         │
│ dispatched_at                   │
│ delivered_at                    │
│                                 │
│ === Analytics Fields ===        │
│ procurement_time                │
│ processing_time                 │
│ dispatch_time_duration          │
│ delivery_time_duration          │
│ total_time                      │
│ sla_breach                      │
│ breached_stage                  │
│ bottleneck_stage                │
│                                 │
│ status                          │
│ created_at                      │
│ updated_at                      │
└─────────────────────────────────┘
```

## Technology Stack

```
┌─────────────────────────────────────────┐
│           Application Layer             │
│  • FastAPI 0.104.1                     │
│  • Uvicorn (ASGI Server)               │
│  • Pydantic 2.5.0 (Validation)         │
└─────────────────────────────────────────┘
                   │
┌─────────────────────────────────────────┐
│          Business Logic Layer           │
│  • Custom Services                     │
│  • Preprocessing Pipeline              │
│  • Analytics Engine                    │
└─────────────────────────────────────────┘
                   │
┌─────────────────────────────────────────┐
│          Data Access Layer              │
│  • SQLAlchemy 2.0.23 (ORM)            │
│  • Alembic (Migrations)                │
└─────────────────────────────────────────┘
                   │
┌─────────────────────────────────────────┐
│            Database Layer               │
│  • SQLite (Dev)                        │
│  • PostgreSQL (Production-ready)       │
└─────────────────────────────────────────┘
```

## Security & Middleware

```
Request
   │
   ▼
┌──────────────────┐
│  CORS Middleware │
│  • Allow origins │
│  • Allow methods │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Logging Middleware│
│  • Request log   │
│  • Response log  │
│  • Timing        │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Error Handling  │
│  • Global catch  │
│  • Validation    │
│  • HTTP errors   │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Authentication  │
│  • Bcrypt hash   │
│  • User verify   │
└────────┬─────────┘
         │
         ▼
    Route Handler
```

## Deployment Architecture

```
┌─────────────────────────────────────────────────┐
│              Production Environment             │
│                                                 │
│  ┌──────────────┐      ┌──────────────┐       │
│  │    Nginx     │─────▶│   Uvicorn    │       │
│  │ Reverse Proxy│      │   Workers    │       │
│  └──────────────┘      └──────┬───────┘       │
│                                │                │
│                        ┌───────▼───────┐       │
│                        │   FastAPI App │       │
│                        │   (main.py)   │       │
│                        └───────┬───────┘       │
│                                │                │
│                        ┌───────▼────────┐      │
│                        │   PostgreSQL   │      │
│                        │    Database    │      │
│                        └────────────────┘      │
└─────────────────────────────────────────────────┘
```

---

**Architecture Type**: Layered Monolith with Service-Oriented Design
**Pattern**: MVC-inspired with Service Layer
**Database**: Relational (SQLAlchemy ORM)
**API Style**: RESTful
**Authentication**: Password-based (Bcrypt)
**Validation**: Pydantic schemas
**Error Handling**: Centralized middleware
