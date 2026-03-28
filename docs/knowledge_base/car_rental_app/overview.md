# Car rental app — overview

## What it is

A full-stack car rental platform built for small-to-medium rental businesses.
The system lets customers browse available vehicles, make reservations,
and pay online — while giving the business owner a management dashboard
to track fleet, bookings, and revenue in real time.

## The problem it solves

Most small car rental businesses in Ethiopia rely on WhatsApp and phone calls
to manage bookings. This leads to double-bookings, lost leads, and zero
visibility into fleet utilization. This app replaces that chaos with
a structured digital workflow.

## Key features

- Vehicle catalog with real-time availability (no double-booking possible)
- Date-range picker with pricing calculator
- Chapa payment gateway integration (Ethiopian local payments)
- Booking confirmation emails via Resend
- Admin dashboard: fleet management, booking calendar, revenue analytics
- Mobile-first responsive design

## Tech stack

- Frontend: Next.js 14, TypeScript, Tailwind CSS
- Backend: Next.js API routes (serverless)
- Database: Supabase (PostgreSQL)
- Auth: Supabase Auth (email + Google OAuth)
- Payments: Chapa API
- Email: Resend
- Deployment: Vercel

## Architecture decisions

**Why Next.js full-stack instead of separate backend?**
The app's data requirements are simple CRUD plus some aggregation queries.
Serverless API routes handle this well without the operational overhead of
a separate Express or FastAPI service. For a client project, this also
keeps the codebase in one repo and deployment in one platform.

**Why Supabase over Firebase?**
PostgreSQL gives proper relational modeling for bookings (vehicle ↔ booking ↔ user).
Firebase's NoSQL would require denormalizing the data in ways that make
availability queries harder. Supabase also provides Row Level Security
out of the box, which simplified the auth logic significantly.

**Why Chapa over Stripe?**
Chapa is the dominant payment gateway in Ethiopia with local bank support.
Stripe is not available for Ethiopian merchants. Chapa's API follows
similar patterns to Stripe, so the integration was straightforward.

## Outcome

Delivered in 6 weeks for a client in Addis Ababa. Currently processing
~40 bookings per month with zero double-bookings since launch.
