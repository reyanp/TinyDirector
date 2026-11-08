"""A tiny HAND-WRITTEN, FICTIONAL labeled set for the heuristic detector (not T-Mobile's dataset, which is not
available here). Written for this repo by the same authors as the rules, so results on it are optimistic and
are NOT a measure of real-world accuracy. Every link uses the reserved .example domain; every number is fictional.

  python -m director.scam.evaluate
"""

from __future__ import annotations

# (label, text): label 1 = scam, 0 = legitimate
EXAMPLES: list[tuple[int, str]] = [
    # --- scams (impersonation, urgency, links, credential lures, bait) ---
    (1, "CarrierAlert: Unusual sign-in detected on your account. Verify within 24 hrs or your service will be "
        "suspended. http://carrier-verify-acct.example/login"),
    (1, "Your mobile account is locked due to suspicious activity. Confirm your identity now: "
        "secure-acct-unlock.example"),
    (1, "USPS: Your package is on hold due to an unpaid redelivery fee. Pay today: usps-redeliver.example/pay"),
    (1, "Congratulations! You've won a $500 gift card from your carrier. Claim your prize: bit.ly/claim-reward"),
    (1, "Bank Security Team: we blocked a payment. Reply with your card number and PIN to restore access."),
    (1, "FINAL NOTICE: your toll balance is overdue. Pay immediately to avoid penalties: tolls-due.example"),
    (1, "Hi, this is the account team. Your bill payment failed. Update your payment info here: "
        "billing-update.example/acct"),
    (1, "Apple ID: your account was compromised. Log in here to keep it active: appleid-secure-login.example"),
    (1, "We noticed an unauthorized charge of $899. If this wasn't you, verify your account at "
        "http://192.0.2.15/verify"),
    (1, "Netflix: your membership will be terminated today. Update your payment details: "
        "netflix-billing-help.example"),
    (1, "Your number is scheduled for disconnection. Call support now and have your account PIN ready."),
    (1, "IRS: you are owed a tax refund. Submit your SSN to claim it: irs-refund-claim.example"),
    (1, "Amazon: suspicious login from a new device. Secure your account immediately: amzn-secure.example/signin"),
    (1, "Your wireless rewards expire tonight! Redeem now at rewards-claim-now.example"),
    (1, "Mom, I lost my phone. This is my new number. Can you send me the code that was just texted to you?"),
    # --- legitimate (including hard look-alikes: real alerts, codes, bills with links) ---
    (0, "Your verification code is 482913. Don't share this code with anyone. We will never call to ask for it."),
    (0, "Reminder: your dentist appointment is tomorrow at 3:30 PM. Reply C to confirm or call the office."),
    (0, "Your bill of $45.00 is ready. View it in your carrier's app. Autopay is on, so no action is needed."),
    (0, "Hey, are we still on for lunch at noon?"),
    (0, "Your order has shipped and should arrive Thursday. Track it in the store's app."),
    (0, "Your prescription is ready for pickup at the pharmacy on Main St."),
    (0, "Thanks for your payment of $60.00. Your account is up to date."),
    (0, "Storm warning: heavy rain expected tonight. Avoid flooded roads."),
    (0, "Your table for 4 at 7 PM is confirmed. See you soon!"),
    (0, "Bank fraud alert: did you make a $25.00 purchase at Corner Cafe? Reply YES or NO. We will never ask "
        "for your PIN."),
    (0, "Your data plan is at 80% for this cycle. Manage your plan in the app."),
    (0, "School closing early today at 1 PM due to weather."),
    (0, "Your library books are due next week. Renew them at the library website."),
    (0, "Your new password was set successfully. If this wasn't you, call the number on the back of your card."),
    (0, "Practice moved to 6 PM on Field 2. Bring water!"),
]
