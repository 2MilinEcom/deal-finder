//
//  ArrivalHubView.swift
//  DEAL SERVICE
//
//  Created by Shopify on 10/2/26.
//

import SwiftUI


struct ArrivalHubView:
    View {

    @State
    private var selectedSection =
        0


    var body:
        some View {

        VStack(
            spacing:
                0
        ) {

            Picker(
                "Arrival Section",
                selection:
                    $selectedSection
            ) {

                Text(
                    "Arrival"
                )
                .tag(
                    0
                )


                Text(
                    "Deals Near Me"
                )
                .tag(
                    1
                )
            }
            .pickerStyle(
                .segmented
            )
            .padding(
                .horizontal
            )
            .padding(
                .top,
                8
            )
            .padding(
                .bottom,
                6
            )


            Divider()


            if selectedSection == 0 {

                ArrivalView()

            } else {

                NearbyDealsView()
            }
        }
    }
}
