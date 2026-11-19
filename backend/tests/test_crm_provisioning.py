from app.crm_provisioning import provision_enquiry_lead, resolved_contact_identity
from app.zoho import ZohoClient


def test_resolved_identity_prefers_bigin_and_reads_books_primary_person():
    identity = resolved_contact_identity(
        {"Full_Name": "Akshat Saxena", "Email": "akshat@example.com", "Phone": "+91 7840000345", "Account_Name": {"name": "Cosmonet"}},
        {"contact_name": "Different label", "contact_persons": [{"email": "books@example.com", "mobile": "7840000000"}]},
    )
    assert identity == {
        "name": "Akshat Saxena",
        "email": "akshat@example.com",
        "phone": "+91 7840000345",
        "company": "Cosmonet",
        "description": "",
    }


class BooksClient(ZohoClient):
    def __init__(self, rows):
        self.rows = rows
        self.created = []

    async def search_books_customers(self, org, query):
        return {"contacts": self.rows}

    async def create_books_contact(self, org, data):
        self.created.append((org, data))
        return {"contact": {"contact_id": "books-new"}}


async def test_books_match_checks_primary_person_email_and_does_not_create():
    client = BooksClient([{"contact_id": "books-existing", "contact_persons": [{"email": "person@example.com", "mobile": "7840000345"}]}])
    result = await client.ensure_books_contact("org", name="New Name", email="person@example.com", phone="+91 7840000345")
    assert result["contact_id"] == "books-existing"
    assert client.created == []


async def test_books_create_payload_includes_walkin_company_and_notes():
    client = BooksClient([])
    result = await client.ensure_books_contact(
        "org",
        name="Akshat Saxena",
        email="akshat@example.com",
        phone="+91 7840000345",
        company="Cosmonet",
        notes="Needs a project quotation",
    )
    assert result["contact_id"] == "books-new"
    assert client.created[0] == (
        "org",
        {
            "contact_name": "Akshat Saxena",
            "contact_type": "customer",
            "customer_sub_type": "business",
            "email": "akshat@example.com",
            "mobile": "7840000345",
            "company_name": "Cosmonet",
            "notes": "Needs a project quotation",
            "contact_persons": [{
                "first_name": "Akshat",
                "last_name": "Saxena",
                "email": "akshat@example.com",
                "mobile": "7840000345",
                "is_primary_contact": True,
            }],
        },
    )


class ProvisionClient:
    def __init__(self):
        self.pipeline_payload = None
        self.books_calls = 0

    async def lookup_bigin_contacts(self, search_by, value):
        return {"data": [{"id": "bigin-existing", "Full_Name": "Canonical Name", "Email": "existing@example.com", "Phone": "7840000345", "Account_Name": {"name": "Existing Co"}, "Description": "Existing details"}]}

    async def update_bigin_contact(self, contact_id, data):
        raise AssertionError("the complete existing contact should not be updated")

    async def create_bigin_contact(self, data):
        raise AssertionError("an exact Bigin match should be reused")

    async def pipeline_layout(self):
        return {"layouts": [{"id": "pipeline-1", "sections": []}]}

    async def create_pipeline(self, data):
        self.pipeline_payload = data
        return {"data": [{"id": "deal-1"}]}


async def test_provision_uses_existing_books_contact_without_second_search_and_links_ids():
    client = ProvisionClient()
    books = {"contact_id": "books-existing", "contact_name": "Canonical Books Name", "email": "books@example.com", "company_name": "Books Co"}
    result = await provision_enquiry_lead(
        client,
        name="Spoken Name",
        phone="7840000345",
        email="spoken@example.com",
        details={"company": "Spoken Co", "description": "Spoken details"},
        books_contact=books,
    )
    assert result["contact"]["id"] == "bigin-existing"
    assert result["books_contact"]["contact_id"] == "books-existing"
    assert result["identity"]["name"] == "Canonical Name"
    assert result["identity"]["company"] == "Existing Co"
    assert client.pipeline_payload["Contact_Name"] == {"id": "bigin-existing"}


class NewBiginClient(ProvisionClient):
    def __init__(self):
        super().__init__()
        self.contact_payload = None

    async def lookup_bigin_contacts(self, search_by, value):
        return {"data": []}

    async def create_bigin_contact(self, data):
        self.contact_payload = data
        return {"data": [{"details": {"id": "bigin-new"}, "id": "bigin-new", "First_Name": data["First_Name"], "Last_Name": data["Last_Name"], "Description": data["Description"]}]}


async def test_provision_creates_bigin_contact_when_no_exact_match():
    client = NewBiginClient()
    result = await provision_enquiry_lead(
        client,
        name="Akshat Saxena",
        phone="7840000345",
        email="akshat@example.com",
        details={"company": "Cosmonet", "requirement": "Project quotation", "description": "Walk-in visitor"},
        books_contact={"contact_id": "books-new", "contact_name": "Akshat Saxena", "email": "akshat@example.com", "phone": "7840000345"},
    )
    assert result["contact"]["id"] == "bigin-new"
    assert client.contact_payload == {
        "First_Name": "Akshat",
        "Last_Name": "Saxena",
        "Email": "akshat@example.com",
        "Phone": "7840000345",
        "Description": "Walk-in visitor\nCompany: Cosmonet\nRequirement: Project quotation",
    }
