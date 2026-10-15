import XCTest

@MainActor
final class DealFinderUITests: XCTestCase {
    func testStoreAccountsAndDigitalReceiptTextReview() {
        let app = XCUIApplication()
        app.launchEnvironment["DEAL_FINDER_TEST_SERVER"] = "http://127.0.0.1:18012"
        app.launch()
        app.tabBars.buttons["Receipts"].tap()
        app.buttons["Add receipt"].tap()
        app.buttons["Store accounts"].firstMatch.tap()
        for name in ["Target", "CVS", "Safeway", "Costco"] {
            XCTAssertTrue(app.buttons[name].exists)
        }
        app.navigationBars.buttons.firstMatch.tap()
        app.buttons["Paste receipt text"].tap()
        let editor = app.textViews["Receipt text"]
        editor.tap()
        editor.typeText("CVS PHARMACY\n10/01/2026\nTOOTHPASTE 6.00\nTOTAL 6.00\n$4.00 off your next $20 purchase\nExpires 10/31/2026")
        app.swipeUp()
        app.buttons["Read receipt text"].tap()
        XCTAssertEqual(app.textFields["Store or business"].value as? String, "CVS")
        for _ in 0..<5 {
            if app.switches["I confirmed this offer is unused and available"].isHittable { break }
            app.swipeUp()
        }
        let confirmation = app.switches["I confirmed this offer is unused and available"]
        XCTAssertTrue(confirmation.exists)
        XCTAssertEqual(confirmation.value as? String, "0")
        XCTAssertTrue(app.textFields["Percent off (if printed)"].exists)
    }
    func testTabsNavigationReceiptReviewAndPersistence() {
        let app = XCUIApplication()
        app.launchEnvironment["DEAL_FINDER_TEST_SERVER"] = "http://127.0.0.1:18012"
        app.launch()
        XCTAssertTrue(app.navigationBars["For You"].waitForExistence(timeout: 20))
        app.tabBars.buttons["Deals"].tap()
        XCTAssertTrue(app.navigationBars["Deals"].exists)
        app.tabBars.buttons["Arrival"].tap()
        XCTAssertTrue(app.buttons["Deals Near Me"].exists)
        app.tabBars.buttons["Household"].tap()
        XCTAssertTrue(app.navigationBars["Household"].exists)
        app.tabBars.buttons["Receipts"].tap()
        app.buttons["Add receipt"].tap()
        XCTAssertTrue(app.navigationBars["Review receipt"].exists)
        let store = app.textFields["Store or business"]
        store.tap(); store.typeText("QA Coffee Shop")
        // Saving a manual reviewed receipt also works without camera availability.
        app.swipeUp(); app.swipeUp(); app.swipeUp()
        let save = app.buttons["Save receipt"]
        XCTAssertTrue(save.waitForExistence(timeout: 5))
        save.tap()
        XCTAssertTrue(app.navigationBars["Receipts"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["QA Coffee Shop"].exists)
        app.terminate(); app.launch()
        app.tabBars.buttons["Receipts"].tap()
        XCTAssertTrue(app.staticTexts["QA Coffee Shop"].waitForExistence(timeout: 5))
        app.staticTexts["QA Coffee Shop"].firstMatch.tap()
        XCTAssertTrue(app.buttons["Correct receipt"].exists)
        app.buttons["Correct receipt"].tap()
        XCTAssertTrue(app.navigationBars["Edit receipt"].exists)
    }
}
