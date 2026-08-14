from sqlalchemy import inspect

from wh_local.modules.product_processing.infrastructure.database import create_database


def test_dimension_canvas_schema_and_preview_revision_exist() -> None:
    database = create_database("sqlite:///:memory:")
    inspector = inspect(database.engine)

    assert {
        "product_processing_dimension_batches",
        "product_processing_dimension_items",
        "product_processing_dimension_assets",
        "product_processing_dimension_change_sets",
        "product_processing_dimension_change_items",
        "product_processing_dimension_notifications",
    }.issubset(set(inspector.get_table_names()))
    draft_columns = {
        column["name"]
        for column in inspector.get_columns("product_processing_drafts")
    }
    item_columns = {
        column["name"]
        for column in inspector.get_columns("product_processing_dimension_items")
    }
    assert "preview_revision" in draft_columns
    assert {
        "render_input_hash",
        "rendered_input_hash",
        "publish_claim_token",
        "publish_claimed_at",
    }.issubset(item_columns)

    database.dispose()


def test_dimension_item_identity_and_change_set_idempotency_are_unique() -> None:
    database = create_database("sqlite:///:memory:")
    inspector = inspect(database.engine)

    item_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(
            "product_processing_dimension_items"
        )
    }
    change_set_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(
            "product_processing_dimension_change_sets"
        )
    }
    assert "uq_dimension_item_identity" in item_constraints
    assert "uq_dimension_change_set_idempotency" in change_set_constraints

    database.dispose()
